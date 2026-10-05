"""Headless answering core: the one code path every caller goes through.

The service is written against seams, never against a provider (principle 2):

    llm          llm.py         chat model            LLM_PROVIDER
    retriever    retrieval.py   website passages      RETRIEVER
    memory       memory.py      conversation state    MEMORY
    guardrail    guardrail.py   content checks        GUARDRAIL
    prompts      prompts/       instructions          PROMPT_SOURCE
    tracing      tracing.py     traces and timings    LANGFUSE_*

`build_components()` assembles them from settings, and the compiled LangGraph
graph is built once per process over them. Tests swap in fakes with `use()`.

Each turn runs the same explicit graph, with one model call for the answer
(and one more to rewrite a follow-up):

    route      rules that run before any model call (`rules.py`): a danger
               to life and a declined request end the turn with a fixed reply;
               then the guardrail checks the question; reports the `behaviour`
    rewrite    a follow-up only: one model call writes it as a standalone
               search from the conversation; never streamed
    retrieve   searches the content; declines a question the content does not
               match (the score floor), and leaves out passages far below the
               best (the gap cut)
    generate   one model call that writes the answer from that content, then
               the guardrail checks it against that content; the turn's sources
               are then the pages the answer cites

A conversation is keyed by `thread_id`: the thread *key* from `threads.py`,
never the signed ID. It is the memory key and the Langfuse session ID.
"""

import asyncio
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph

from assistant import config, guardrail, llm, memory, prompts, retrieval, rules, tracing

log = logging.getLogger("ai-starter")

# Reasoning models differ in where they put their chain-of-thought. With
# `reasoning=True` Ollama returns it in a separate field, but some models still
# emit it inline in content wrapped in <think>...</think>, which would otherwise
# be shown to the user as though it were the answer.
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


def strip_reasoning(text: str) -> str:
    """Remove inline <think>...</think> blocks from a completed answer.

    An unterminated block means the model ran out of tokens mid-thought and never
    produced an answer; returning the raw reasoning would be worse than returning
    nothing, so it is dropped and the caller sees an empty answer.
    """
    if _THINK_OPEN not in text:
        return text
    out = []
    rest = text
    while _THINK_OPEN in rest:
        before, _, after = rest.partition(_THINK_OPEN)
        out.append(before)
        if _THINK_CLOSE in after:
            _, _, rest = after.partition(_THINK_CLOSE)
        else:
            rest = ""  # unterminated: discard the trailing thought
    out.append(rest)
    # strip(), not lstrip(): a block at the end leaves trailing whitespace behind.
    return "".join(out).strip()


class ReasoningFilter:
    """Streaming equivalent of `strip_reasoning`.

    Tags can straddle chunk boundaries, so partial-tag text is held back rather
    than emitted and corrected later. While inside a think block nothing is
    emitted at all, so the caller keeps showing its "working" state until real
    answer text begins.
    """

    def __init__(self) -> None:
        self._in_think = False
        self._pending = ""

    def feed(self, chunk: str) -> str:
        self._pending += chunk
        out = []
        while self._pending:
            if self._in_think:
                if _THINK_CLOSE in self._pending:
                    _, _, self._pending = self._pending.partition(_THINK_CLOSE)
                    self._in_think = False
                    continue
                # Keep only enough to recognise a close tag split across chunks.
                self._pending = self._pending[-len(_THINK_CLOSE) :]
                break
            if _THINK_OPEN in self._pending:
                before, _, after = self._pending.partition(_THINK_OPEN)
                out.append(before)
                self._pending = after
                self._in_think = True
                continue
            # Hold back a possible partial opening tag; emit the rest.
            hold = len(_THINK_OPEN) - 1
            if len(self._pending) > hold:
                out.append(self._pending[:-hold])
                self._pending = self._pending[-hold:]
            break
        return "".join(out)

    def flush(self) -> str:
        """Emit anything still held back once the stream ends."""
        if self._in_think:
            self._pending = ""
            return ""
        tail, self._pending = self._pending, ""
        return tail


# ---------------------------------------------------------------- components


@dataclass
class Components:
    """One implementation per seam. Construction must make no network call."""

    llm: BaseChatModel
    model: str  # the model ID reported to callers
    retriever: retrieval.Retriever
    checkpointer: BaseCheckpointSaver
    guardrail: guardrail.Guardrail
    prompts: prompts.PromptSource


def build_components(settings: config.Settings) -> Components:
    return Components(
        llm=llm.get_chat_model(settings),
        model=llm.model_name(settings),
        retriever=retrieval.get_retriever(settings),
        checkpointer=memory.get_checkpointer(settings),
        guardrail=guardrail.get_guardrail(settings),
        prompts=prompts.get_prompt_source(settings),
    )


# RLock, not Lock: the getters nest. get_agent() holds the lock and calls
# get_components(), which takes it again. A plain Lock is not reentrant, so
# that combination deadlocks the first question every time (and looks like a
# hung request, not a crash).
_lock = threading.RLock()
_components: Components | None = None
_agent = None


def get_components() -> Components:
    global _components
    with _lock:
        if _components is None:
            _components = build_components(config.settings)
        return _components


def use(components: Components | None) -> None:
    """Replace the components (tests use fakes); None rebuilds from settings."""
    global _components, _agent
    with _lock:
        _components = components
        _agent = None


def get_agent():
    """Compiled answering graph, cached per process."""
    global _agent
    with _lock:
        if _agent is None:
            _agent = build_graph(get_components())
        return _agent


# -------------------------------------------------------------------- graph

# The graph's run name, which is also the trace name in Langfuse, and the traces' tags.
RUN_NAME = "assistant"
TRACE_TAGS = ["ai-starter"]

# Node names, which the streaming events are matched on.
ROUTE = "route"
REWRITE = "rewrite"
RETRIEVE = "retrieve"
GENERATE = "generate"

# Reported when the turn is declined: by the rules, the score floor or the guardrail.
BLOCKED_BEHAVIOUR = "decline"
# Reported when the answer is one clarifying question.
CLARIFY_BEHAVIOUR = "clarify"
# Reported for the fixed emergency reply.
EMERGENCY_BEHAVIOUR = "emergency"
# Reported for complaints and account requests, answered with the contact route.
HANDOFF_BEHAVIOUR = "handoff"
# Behaviours whose fixed reply from `route` or `retrieve` ends the turn without a model call.
FIXED_BEHAVIOURS = (EMERGENCY_BEHAVIOUR, BLOCKED_BEHAVIOUR)


class TurnState(MessagesState):
    """Conversation state.

    `behaviour` and `sources` describe the latest turn. `danger` is set by a turn that found a
    danger to life, so the next turn repeats the safety reply ("Is it really that serious?"),
    and `check_scope` tells `retrieve` to decline a question the content does not match.
    `search` is what `retrieve` searches for: the message, or the rewritten follow-up; empty for
    no search. `prompts` names the prompts the latest turn used, each with its Langfuse version
    or "file", and `guardrail` what the guardrail found in the latest turn: each check's
    `decisions` (`tracing.guardrail_decision`), the `findings`, the grounding `scores`, and
    `blocked` ("input" or "output") when it blocked.

    Per-conversation user context (a region, a customer segment) goes here too, kept across
    turns, and filters the search in `retrieve`.
    """

    behaviour: str
    sources: list[dict]
    danger: bool
    check_scope: bool
    search: str
    rewrite: bool
    prompts: dict[str, int | str]
    guardrail: dict


def to_sources(passages: list) -> list[dict]:
    """The retrieved passages as the turn's `sources`.

    Each page gets a number, `page`, in order of first appearance; the model cites a page by it
    ("[1]") and the callers turn that into the page's link, so the model never writes a title or
    a URL as a citation. A page is its URL, or its title when it has none. A URL outside
    `ALLOWED_LINK_PREFIX` is dropped, so no caller can link it.
    """
    pages: dict[str, int] = {}
    sources = []
    for i, p in enumerate(passages):
        page = pages.setdefault(p.url or p.source, len(pages) + 1)
        sources.append(
            {"source": p.source, "url": linkable(p.url), "page": page, "chunk_index": i, "content": p.content}
        )
    return sources


def linkable(url: str | None) -> str | None:
    """`url` if it is on the client's site (`ALLOWED_LINK_PREFIX`), else None."""
    prefix = config.settings.allowed_link_prefix
    return url if url and prefix and url.startswith(prefix) else None


def format_content(sources: list[dict]) -> str:
    """The retrieved passages as the model reads them."""
    if not sources:
        return "No content was found for the customer's latest message."
    passages = "\n\n---\n\n".join(f"{_label(s)}\n{s['content']}" for s in sources)
    return f"Content found for the customer's latest message:\n\n{passages}"


# "[1]", "[1, 3]" or "【1】", as the callers link them; not a Markdown link's text ("[1](…)").
_CITATION = re.compile(r"[\[【](\d+(?:\s*,\s*\d+)*)[\]】](?!\()")


def cited(sources: list[dict], answer: str) -> list[dict]:
    """The sources from the pages the answer cites: the others were retrieved but not used.

    An answer that cites no page keeps them all: some models leave out the citations, and the
    answer is still drawn from that content.
    """
    pages = {int(n) for group in _CITATION.findall(answer) for n in group.split(",")}
    return [s for s in sources if s["page"] in pages] if pages else sources


def asks(answer: str) -> bool:
    """Whether the answer is a clarifying question: one sentence, a question, citing nothing."""
    text = answer.strip()
    return text.endswith("?") and not _CITATION.search(text) and not re.search(r"[.!?]\s", text)


def _label(source: dict) -> str:
    return f"[{source['page']}] {source['source'] or source['url'] or 'Untitled page'}"


def used(state, *parts: prompts.Prompt) -> dict[str, int | str]:
    """The turn's prompts so far, with these added: name -> Langfuse version, or "file"."""
    return {**(state.get("prompts") or {}), **{p.name: p.served for p in parts}}


def checked(state, result: guardrail.GuardrailResult, source: str) -> dict:
    """The turn's guardrail report so far, with this check's added (AD-18)."""
    report = dict(state.get("guardrail") or {})
    report["decisions"] = {**report.get("decisions", {}), source: tracing.guardrail_decision(result)}
    findings = [*report.get("findings", []), *(f for f in result.findings if f not in report.get("findings", []))]
    if findings:
        report["findings"] = findings
    if result.scores:
        report["scores"] = {**report.get("scores", {}), **result.scores}
    if not result.allowed:
        report["blocked"] = source
    return report


def turn_decision(report: dict | None) -> str | None:
    """The guardrail's decision for a turn, as the trace's `guardrail` score: `blocked_input`,
    `blocked_output`, `masked`, `flagged` or `allowed`; None when nothing was checked (a fixed
    reply)."""
    report = report or {}
    if report.get("blocked"):
        return f"blocked_{report['blocked']}"
    decisions = report.get("decisions", {})
    for decision in ("masked", "flagged", "allowed"):
        if decision in decisions.values():
            return decision
    return None


def build_graph(components: Components):
    """Route, then (for a follow-up) rewrite, then retrieve, then one model call."""
    # Nodes that take the run's `config` cannot reach the settings by that name.
    settings = config.settings

    def fixed(state: TurnState, behaviour: str, name: str, sources: list[dict] | None = None, **fields) -> dict:
        """A turn that ends with a fixed reply, with no model call."""
        prompt = components.prompts.get_prompt(name)
        return {
            "behaviour": behaviour,
            "sources": sources or [],
            "messages": [AIMessage(content=prompt.text)],
            "prompts": used(state, prompt),
            **fields,
        }

    def linked(prompt: prompts.Prompt):
        """The chat model, linked in the trace to the Langfuse prompt that instructs it.

        Langfuse links a model call to the prompt named in a sibling run's metadata, hence the
        pass-through step before the model.
        """
        if prompt.ref is None:
            return components.llm
        step = RunnableLambda(lambda messages: messages, name="langfuse-prompt")
        return step.with_config(metadata={"langfuse_prompt": prompt.ref}) | components.llm

    def route(state: TurnState, config: RunnableConfig) -> dict:
        # The rules, in order: a danger to life gets the fixed safety reply before anything else;
        # then declines; then complaints and account requests, answered with the handoff prompt.
        message = state["messages"][-1].content
        rule = rules.classify(message, danger=settings.safety_rules)
        # The turn after a danger repeats the safety reply, once, unless it is a new one.
        if rule == "danger" or (state.get("danger") and rule is None):
            page = rules.EMERGENCY_PAGE
            cites = [{**page, "page": 1, "chunk_index": 0, "content": ""}] if page else []
            return fixed(state, EMERGENCY_BEHAVIOUR, "emergency", cites, danger=rule == "danger")
        if rule == "decline":
            return fixed(state, BLOCKED_BEHAVIOUR, "decline", danger=False)
        # Only a question that goes on to the model is checked: the rules' replies never depend
        # on the guardrail. A masked question replaces the customer's, in memory too.
        result = tracing.observe_guardrail(config, "input", lambda: components.guardrail.check(message, source="input"))
        report = checked(state, result, "input")
        if not result.allowed:
            return {
                "behaviour": BLOCKED_BEHAVIOUR,
                "sources": [],
                "messages": [AIMessage(content=result.message)],
                "danger": False,
                "guardrail": report,
            }
        masked = []
        if result.text is not None:
            message = result.text
            masked = [HumanMessage(content=message, id=state["messages"][-1].id)]
        # A conversation's first question, or one after a decline, is checked against the content.
        # A follow-up is checked once `rewrite` has made it a standalone question.
        first = state.get("behaviour") in (None, BLOCKED_BEHAVIOUR)
        earlier = any(isinstance(m, HumanMessage) for m in state["messages"][:-1])
        return {
            "behaviour": HANDOFF_BEHAVIOUR if rule == "handoff" else "answer",
            "danger": False,
            "check_scope": first,
            "search": message,
            "rewrite": earlier and settings.rewrite_follow_ups,
            "guardrail": report,
            **({"messages": masked} if masked else {}),
        }

    def after_route(state: TurnState) -> str:
        if state["behaviour"] in FIXED_BEHAVIOURS:
            return END
        return REWRITE if state["rewrite"] else RETRIEVE

    def rewrite_input(state: TurnState) -> tuple[list, prompts.Prompt]:
        # The latest turns as a transcript, so the model reads the conversation as data, not as
        # a conversation of its own to continue. Long answers are cut: the topic is in the start.
        lines = []
        for m in state["messages"][-7:-1]:
            speaker = "Customer" if isinstance(m, HumanMessage) else "Assistant"
            text = " ".join(str(m.content).split())
            lines.append(f"{speaker}: {text[:400]}")
        latest = state["messages"][-1].content
        transcript = "\n".join(lines)
        prompt = components.prompts.get_prompt("rewrite")
        return [
            SystemMessage(content=prompt.text),
            HumanMessage(content=f"Conversation so far:\n{transcript}\n\nLatest message: {latest}"),
        ], prompt

    def rewritten(state: TurnState, message) -> dict:
        text = strip_reasoning(message.text).strip()
        query = text.splitlines()[0].strip().strip("\"'") if text else ""
        if query.upper().rstrip(".") == "NONE":
            return {"search": "", "check_scope": False}
        if not query or len(query) > 400:
            return {}  # keep the message as the search
        # A standalone question can be judged on its own, so a follow-up off the topic is declined.
        return {"search": query, "check_scope": True}

    def rewrite(state: TurnState, config: RunnableConfig) -> dict:
        try:
            messages, prompt = rewrite_input(state)
            return {**rewritten(state, linked(prompt).invoke(messages, config)), "prompts": used(state, prompt)}
        except Exception:  # noqa: BLE001 — a failed rewrite searches the message instead
            log.warning("rewrite failed; searching the message as it is", exc_info=True)
            return {}

    async def arewrite(state: TurnState, config: RunnableConfig) -> dict:
        try:
            messages, prompt = rewrite_input(state)
            return {**rewritten(state, await linked(prompt).ainvoke(messages, config)), "prompts": used(state, prompt)}
        except Exception:  # noqa: BLE001 — a failed rewrite searches the message instead
            log.warning("rewrite failed; searching the message as it is", exc_info=True)
            return {}

    def retrieve(state: TurnState) -> dict:
        message = state["messages"][-1].content
        search = state.get("search", message)
        passages = components.retriever.search(search) if search else []
        scores = [p.score for p in passages if p.score is not None]
        floor = config.settings.min_score
        # A short message ("thanks", "yes") is left to the model: it has too few words to score.
        scoped = state.get("check_scope") and state["behaviour"] == "answer" and len(message.split()) > 3
        if scoped and floor and scores and max(scores) < floor:
            return fixed(state, BLOCKED_BEHAVIOUR, "decline")
        # Passages far below the best are about something else: leave them out of the input.
        gap = config.settings.max_gap
        if gap and scores:
            passages = [p for p in passages if p.score is None or p.score >= max(scores) - gap]
        return {"sources": to_sources(passages)}

    def after_retrieve(state: TurnState) -> str:
        return END if state["behaviour"] == BLOCKED_BEHAVIOUR else GENERATE

    def model_input(state: TurnState) -> tuple[list, list[prompts.Prompt]]:
        # Read on every turn, so a prompt changed in Langfuse applies to the
        # next turn without a restart. The content goes in the system message
        # rather than the history, so memory holds only the conversation.
        get = components.prompts.get_prompt
        system = get("system")
        after = [get("handoff")] if state["behaviour"] == HANDOFF_BEHAVIOUR else []
        parts = [system.text, format_content(state["sources"]), *(p.text for p in after)]
        return [SystemMessage(content="\n\n".join(parts)), *state["messages"]], [system, *after]

    def reply(state: TurnState, message) -> dict:
        # `.text`, not `.content`: Bedrock's Converse returns a list of blocks, reasoning among them.
        text = strip_reasoning(message.text)
        behaviour = state["behaviour"]
        sources = cited(state["sources"], text) if config.settings.cited_sources_only else state["sources"]
        # The model asked a clarifying question rather than answering: it cites no pages.
        if behaviour == "answer" and asks(text):
            behaviour, sources = CLARIFY_BEHAVIOUR, []
        return {"messages": [AIMessage(content=text, id=message.id)], "sources": sources, "behaviour": behaviour}

    def check_reply(state: TurnState, out: dict, config: RunnableConfig) -> dict:
        # The answer is checked against the passages it was written from (the grounding check),
        # except a clarifying question, which cites none. A blocked answer is replaced, in memory
        # too, so the next turn's model never reads it.
        answer = out["messages"][0]
        sources = state["sources"] if out["behaviour"] != CLARIFY_BEHAVIOUR else []
        grounding = "\n\n".join(s["content"] for s in sources if s["content"]) or None
        query = state.get("search") or state["messages"][-1].content
        result = tracing.observe_guardrail(
            config,
            "output",
            lambda: components.guardrail.check(answer.content, source="output", grounding=grounding, query=query),
        )
        out = {**out, "guardrail": checked(state, result, "output")}
        if not result.allowed:
            blocked = AIMessage(content=result.message, id=answer.id)
            out |= {"messages": [blocked], "sources": [], "behaviour": BLOCKED_BEHAVIOUR}
        return out

    # The answer's trace links to the system prompt, the one every answer uses.
    def generate(state: TurnState, config: RunnableConfig) -> dict:
        messages, parts = model_input(state)
        out = reply(state, linked(parts[0]).invoke(messages, config))
        return {**check_reply(state, out, config), "prompts": used(state, *parts)}

    async def agenerate(state: TurnState, config: RunnableConfig) -> dict:
        messages, parts = model_input(state)
        out = reply(state, await linked(parts[0]).ainvoke(messages, config))
        # The check is a blocking call: off the event loop, so other streams carry on.
        return {**(await asyncio.to_thread(check_reply, state, out, config)), "prompts": used(state, *parts)}

    graph = StateGraph(TurnState)
    graph.add_node(ROUTE, route)
    graph.add_node(REWRITE, RunnableLambda(rewrite, afunc=arewrite))
    graph.add_node(RETRIEVE, retrieve)
    graph.add_node(GENERATE, RunnableLambda(generate, afunc=agenerate))
    graph.add_edge(START, ROUTE)
    graph.add_conditional_edges(ROUTE, after_route, [REWRITE, RETRIEVE, END])
    graph.add_edge(REWRITE, RETRIEVE)
    graph.add_conditional_edges(RETRIEVE, after_retrieve, [GENERATE, END])
    graph.add_edge(GENERATE, END)
    return graph.compile(checkpointer=components.checkpointer)


# ------------------------------------------------------------------ helpers


def new_thread_id() -> str:
    return str(uuid.uuid4())


# Shown by the callers (the API, the AgentCore entry point). When a run fails, the details go to
# the log, not the response: provider errors carry internals a customer should never see.
ERROR_MESSAGE = "Something went wrong while answering. Please try again."
TURN_LIMIT_MESSAGE = "This conversation has reached its limit. Please start a new one."


class TurnLimitReached(Exception):
    """The conversation has used `MAX_TURNS_PER_THREAD` turns."""


def turns_used(thread_id: str) -> int:
    """Number of questions already asked in a thread, read from its memory."""
    state = get_agent().get_state({"configurable": {"thread_id": thread_id}})
    return sum(1 for msg in state.values.get("messages", []) if isinstance(msg, HumanMessage))


def _check_turn_limit(thread_id: str) -> None:
    # Caps token spend per conversation; a long thread also re-sends its whole
    # history to the model on every turn.
    if turns_used(thread_id) >= config.settings.max_turns_per_thread:
        raise TurnLimitReached(thread_id)


def build_config(thread_id: str, metadata: dict | None = None):
    """LangChain invoke config, with a Langfuse handler when tracing is enabled.

    Returns `(config, handler)`; `handler` is None when tracing is off.
    """
    handler = tracing.handler(config.settings)
    run_config = {
        "configurable": {"thread_id": thread_id},
        # The Langfuse handler propagates these to every observation of the turn.
        "metadata": {
            "langfuse_session_id": thread_id,
            "langfuse_tags": TRACE_TAGS,
            "langfuse_trace_name": RUN_NAME,
            **(metadata or {}),
        },
        "run_name": RUN_NAME,
    }
    if handler is not None:
        run_config["callbacks"] = [handler]
    return run_config, handler


def _traced(thread_id: str):
    """Puts the turn's session, tags and trace name on every Langfuse observation."""
    return tracing.attributes(config.settings, session_id=thread_id, tags=TRACE_TAGS, trace_name=RUN_NAME)


class Timings:
    """Milliseconds from the start of a turn to each milestone (principle 10).

    `first_status_ms` is when the caller first sees progress, `first_token_ms`
    when answer text starts to appear, and `total_ms` when the answer is
    complete. The first two are None on the blocking path.
    """

    def __init__(self, clock=time.perf_counter) -> None:
        self._clock = clock
        self._start = clock()
        self.first_status_ms: int | None = None
        self.first_token_ms: int | None = None
        self.total_ms: int | None = None

    def _since_start(self) -> int:
        return round((self._clock() - self._start) * 1000)

    def status(self) -> None:
        if self.first_status_ms is None:
            self.first_status_ms = self._since_start()

    def token(self) -> None:
        if self.first_token_ms is None:
            self.first_token_ms = self._since_start()

    def finish(self) -> dict[str, int | None]:
        self.total_ms = self._since_start()
        return {
            "first_status_ms": self.first_status_ms,
            "first_token_ms": self.first_token_ms,
            "total_ms": self.total_ms,
        }


def _record(thread_id: str, handler, timings: Timings, used_prompts: dict | None, report: dict | None) -> None:
    values = timings.finish()
    report = report or {}
    found = [*report.get("findings", []), *([f"blocked:{report['blocked']}"] if "blocked" in report else [])]
    log.info(
        "answered thread=%s %s prompts=%s guardrail=%s",
        thread_id,
        " ".join(f"{name}={value}" for name, value in values.items()),
        ",".join(f"{name}:{served}" for name, served in (used_prompts or {}).items()) or "-",
        ",".join(found) or "-",
    )
    # The grounding scores go to the trace beside the timings, for the evaluation to tune (AD-18).
    scores = {f"guardrail_{name}": round(value, 3) for name, value in report.get("scores", {}).items()}
    tracing.record_timings(config.settings, tracing.trace_id(handler), {**values, **scores}, turn_decision(report))


# -------------------------------------------------------------------- public


def _turn_input(question: str) -> dict:
    return {"messages": [HumanMessage(content=question)], "prompts": {}, "guardrail": {}}


def answer(question: str, thread_id: str | None = None, metadata: dict | None = None) -> dict:
    """Answer a customer question.

    Returns `{"answer", "sources", "behaviour", "thread_id", "trace_id", "model"}`.
    Pass the returned `thread_id` back on the next turn to keep context.
    Raises `TurnLimitReached` when the thread has no turns left.
    """
    if not question or not question.strip():
        raise ValueError("question must not be empty")

    timings = Timings()
    thread_id = thread_id or new_thread_id()
    _check_turn_limit(thread_id)
    model = get_components().model

    agent = get_agent()
    run_config, handler = build_config(thread_id, metadata=metadata)
    with _traced(thread_id):
        result = agent.invoke(_turn_input(question), config=run_config)
    answer_text = result["messages"][-1].content
    _record(thread_id, handler, timings, result.get("prompts"), result.get("guardrail"))

    return {
        "answer": answer_text,
        "sources": result["sources"],
        "behaviour": result["behaviour"],
        "thread_id": thread_id,
        "trace_id": tracing.trace_id(handler),
        "model": model,
    }


class _StreamedTurn:
    """Turns the graph's LangGraph events into the service's stream events."""

    def __init__(self, timings: Timings) -> None:
        self.timings = timings
        self.sources: list[dict] = []
        self.behaviour: str | None = None
        self.answer = ""
        self.prompts: dict[str, int | str] = {}
        self.guardrail: dict = {}
        self._searching = False
        self._answered = False
        self._reasoning = ReasoningFilter()

    def handle(self, event: dict) -> list[dict]:
        kind = event.get("event")
        # A node's own events carry its name. Match on both, so a runnable
        # nested inside a node never counts as the node itself.
        node = event.get("metadata", {}).get("langgraph_node")
        is_node = event.get("name") == node
        if kind == "on_chain_end" and is_node:
            output = event["data"].get("output")
            if isinstance(output, dict):
                self.prompts.update(output.get("prompts") or {})
                self.guardrail = output.get("guardrail") or self.guardrail

        if kind == "on_chain_end" and is_node and node == ROUTE:
            output = event["data"]["output"]
            self.behaviour = output["behaviour"]
            out = []
            # A fixed reply from the rules or the guardrail is the turn's answer, with the page it
            # cites, if any. (The other message `route` may return is the masked question.)
            replies = [m for m in output.get("messages", []) if isinstance(m, AIMessage)]
            if replies:
                self.sources = output["sources"]
                if self.sources:
                    out.append({"type": "sources", "sources": self.sources})
            for message in replies:
                out += self._token(message.content)
            return out
        elif kind == "on_chain_start" and is_node and node in (REWRITE, RETRIEVE):
            # Rewriting a follow-up is part of the search, as the customer sees it.
            if self._searching:
                return []
            self._searching = True
            self.timings.status()
            return [{"type": "status", "stage": "searching"}]
        elif kind == "on_chain_end" and is_node and node == RETRIEVE:
            output = event["data"]["output"]
            if output.get("messages"):  # declined: the fixed reply is the answer
                self.behaviour = output["behaviour"]
                self.sources = output["sources"]
                out = [{"type": "sources", "sources": self.sources}]
                for message in output["messages"]:
                    out += self._token(message.content)
                return out
            return [{"type": "status", "stage": "generating"}]
        elif kind == "on_chain_end" and is_node and node == GENERATE and not self._answered:
            # The sources follow the answer: they are the pages it cites. The node and the
            # runnable inside it both end with this name, so only the first counts.
            self._answered = True
            output = event["data"]["output"]
            self.behaviour = output["behaviour"]
            self.sources = output["sources"]
            out = self.finish()
            # The guardrail blocked the answer after it was streamed (AD-18): the caller replaces
            # what it showed with the fixed message.
            if (output.get("guardrail") or {}).get("blocked") == "output":
                self.answer = output["messages"][0].content
                out.append({"type": "replace", "text": self.answer})
            return [*out, {"type": "sources", "sources": self.sources}]
        elif kind == "on_chat_model_stream" and node == GENERATE:
            # `.text` is the answer text whether the provider streams a str (Ollama) or a list of
            # content blocks (Bedrock's Converse, whose reasoning blocks it leaves out).
            chunk = event["data"].get("chunk")
            return self._token(self._reasoning.feed(chunk.text if chunk is not None else ""))
        return []

    def finish(self) -> list[dict]:
        return self._token(self._reasoning.flush())

    def _token(self, visible: str) -> list[dict]:
        if not visible:
            return []
        self.timings.token()
        self.answer += visible
        return [{"type": "token", "text": visible}]


async def astream_answer(question: str, thread_id: str | None = None, metadata: dict | None = None):
    """Async generator yielding progress events for the same run as `answer`.

    Event shapes (all dicts with a `type`):
      {"type": "start",  "thread_id": ...}
      {"type": "status", "stage": "searching" | "generating"}
      {"type": "token",  "text": "..."}                  incremental answer text
      {"type": "replace", "text": "..."}                 the guardrail blocked the answer
                                                  streamed so far: show this instead
      {"type": "sources","sources": [...]}               the pages the answer cites:
                                                  before a fixed reply, after a
                                                  written one
      {"type": "done",   "answer": ..., "behaviour": ..., "trace_id": ..., ...}
      {"type": "error",  "message": ...}

    Raises `TurnLimitReached` before the `start` event when the thread has no
    turns left.
    """
    if not question or not question.strip():
        raise ValueError("question must not be empty")

    timings = Timings()
    thread_id = thread_id or new_thread_id()
    _check_turn_limit(thread_id)
    model = get_components().model

    yield {"type": "start", "thread_id": thread_id, "model": model}

    agent = get_agent()
    run_config, handler = build_config(thread_id, metadata=metadata)
    turn = _StreamedTurn(timings)

    with _traced(thread_id):
        async for event in agent.astream_events(_turn_input(question), config=run_config, version="v2"):
            for out in turn.handle(event):
                yield out
    for out in turn.finish():
        yield out

    _record(thread_id, handler, timings, turn.prompts, turn.guardrail)

    yield {
        "type": "done",
        "answer": turn.answer,
        "sources": turn.sources,
        "behaviour": turn.behaviour,
        "thread_id": thread_id,
        "trace_id": tracing.trace_id(handler),
        "model": model,
    }


def health() -> dict:
    """Dependency check for the API's /api/health endpoint. Never raises."""
    settings = config.settings
    status: dict = {}
    errors = []
    for part in (llm.health(settings), retrieval.health(settings)):
        if "error" in part:
            errors.append(part.pop("error"))
        status |= part
    # Read the cached components rather than build them: health must stay cheap.
    retriever = _components.retriever if _components is not None else None
    status |= {
        "embeddings_loaded": getattr(retriever, "loaded", False),
        "memory": settings.memory,
        "tracing": settings.tracing_enabled(),
        "prompts": _components.prompts.status() if _components is not None else {"source": settings.prompt_source},
    }
    if errors:
        status["errors"] = errors
    return status
