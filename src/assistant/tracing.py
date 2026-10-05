"""The `tracing` seam: Langfuse traces with PII masked, plus answer timings.

Tracing is on only when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are
set. Locally that is the Langfuse in the `tracing` Compose profile; in the cloud
it is a project per environment in Crystal Delta's Langfuse (Phase 6). The SDK is v4 (4.7 or later,
which Langfuse Cloud shows in real time); it also works with the local
`langfuse:3` server (3.63 or later).

Every trace passes through `mask_pii` before it leaves the process
(principle 7). A guardrail, which also masks PII, arrives late (DevOps stage 2),
so this mask is what protects traces until then.
"""

import contextlib
import logging
import re
import threading

from assistant.config import Settings

log = logging.getLogger("ai-starter")

# --------------------------------------------------------------- PII masking
#
# Biased towards masking too much: a trace with a date or reference masked is
# still useful, a trace with a customer's phone number in it is an incident.
# Order matters: emails and phone numbers are matched before the generic digit
# run, so they get the more specific label.

_STREET_TYPES = (
    "street|st|road|rd|avenue|ave|drive|dr|court|ct|place|pl|crescent|cres|lane|ln|parade|pde"
    "|terrace|tce|highway|hwy|boulevard|blvd|way|close|cl|circuit|cct|grove|gr|square|sq"
)

_PATTERNS = [
    ("[email]", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    # Australian phone numbers: 04xx xxx xxx, 0x xxxx xxxx, (0x) xxxx xxxx and
    # +61 forms. 13, 1300 and 1800 numbers are businesses', not people's.
    (
        "[phone]",
        re.compile(r"(?:\+61[\s-]?\(?0?\)?|\(0|\b0)[2-478]\)?(?:[\s-]?\d){8}\b"),
    ),
    # Street addresses: a number, one to three words and a street type,
    # e.g. "12 Smith St", "4/88 Mount Pleasant Road".
    (
        "[address]",
        re.compile(
            rf"\b\d{{1,5}}[a-z]?(?:/\d{{1,5}}[a-z]?)?\s+(?:[a-z'-]+\s+){{1,3}}(?:{_STREET_TYPES})\b\.?",
            re.IGNORECASE,
        ),
    ),
    # Account, card, member and meter numbers: any run of 8 or more digits,
    # allowing spaces or hyphens between them. 1300 and 1800 numbers are left
    # alone, so a trace still shows whether the bot quoted a real one.
    ("[number]", re.compile(r"\b(?!1[38]00[\s-]?\d{3}[\s-]?\d{3}\b)\d(?:[\s-]?\d){7,}\b")),
]

# UUIDs (run, tool-call and message IDs) are random and hold no PII, but an
# all-digit group inside one looks like an account number. Masking it breaks
# the IDs that link a trace together, so UUIDs are left whole.
_UUID = re.compile(r"(\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b)", re.IGNORECASE)


def _mask(text: str) -> str:
    for label, pattern in _PATTERNS:
        text = pattern.sub(label, text)
    return text


def mask_text(text: str) -> str:
    # split() with a capturing group puts the UUIDs at the odd indexes.
    return "".join(part if i % 2 else _mask(part) for i, part in enumerate(_UUID.split(text)))


def mask_pii(*, data, **_):
    """Langfuse mask function: redact PII from anything a trace carries.

    Langfuse passes inputs, outputs and metadata, which may be strings, nested
    dicts and lists, or LangChain objects such as messages.
    """
    if isinstance(data, str):
        return mask_text(data)
    if isinstance(data, dict):
        return {key: mask_pii(data=value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [mask_pii(data=item) for item in data]
    if data is None or isinstance(data, (bool, int, float)):
        return data
    if hasattr(data, "model_dump"):  # pydantic models, including LangChain messages
        return mask_pii(data=data.model_dump())
    return mask_text(str(data))


# ------------------------------------------------------------------- client

_lock = threading.Lock()
_client = None


def client(settings: Settings):
    """The process's Langfuse client, created once with the PII mask.

    The LangChain `CallbackHandler` finds this client by its public key, so it
    must exist before the first handler is made.
    """
    global _client
    if not settings.tracing_enabled():
        raise ValueError("Langfuse needs LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY")
    with _lock:
        if _client is None:
            from langfuse import Langfuse

            _client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
                mask=mask_pii,
            )
        return _client


def handler(settings: Settings):
    """A LangChain callback handler for one run, or None when tracing is off."""
    if not settings.tracing_enabled():
        return None
    client(settings)
    from langfuse.langchain import CallbackHandler

    return CallbackHandler(public_key=settings.langfuse_public_key)


def attributes(settings: Settings, *, session_id: str, tags: list[str], trace_name: str):
    """A block whose Langfuse observations all carry the session, tags and trace name.

    The handler applies the run's `langfuse_*` metadata at the root run only on
    the async stream (SDK v4), so without this the generations, which carry the
    cost, have no session. A no-op when tracing is off.
    """
    if not settings.tracing_enabled():
        return contextlib.nullcontext()
    client(settings)
    from langfuse import propagate_attributes

    return propagate_attributes(session_id=session_id, tags=tags, trace_name=trace_name)


def observe_guardrail(run_config, source: str, check):
    """Run a guardrail check (`check()`, which returns a `GuardrailResult`) as a `guardrail`
    observation under the node that runs it, and return its result (AD-18).

    The observation records the decision, the types found and the grounding scores, never the
    text, and is a WARNING when the check blocked. The node's observation is found through the
    run's Langfuse handler: on the async stream the handler runs on a worker thread, so the
    node's span is not the current one. Without tracing, or if anything here fails, the check
    still runs.
    """
    parent = _node_observation(run_config)
    if parent is None:
        return check()
    try:
        span = parent.start_observation(name=f"guardrail-{source}", as_type="guardrail", input={"source": source})
    except Exception:  # noqa: BLE001 — tracing must never break answering
        log.warning("could not trace the guardrail check", exc_info=True)
        return check()
    try:
        result = check()
        span.update(
            output={"decision": guardrail_decision(result), "findings": list(result.findings), "scores": result.scores},
            level="DEFAULT" if result.allowed else "WARNING",
        )
        return result
    finally:
        span.end()


def guardrail_decision(result) -> str:
    """One word for a check's result: blocked, masked, flagged (found, no action) or allowed."""
    if not result.allowed:
        return "blocked"
    if result.text is not None:
        return "masked"
    return "flagged" if result.findings else "allowed"


def _node_observation(run_config):
    """The Langfuse observation of the node a run config belongs to, or None."""
    callbacks = (run_config or {}).get("callbacks")
    run_id = getattr(callbacks, "parent_run_id", None)
    for run_handler in getattr(callbacks, "handlers", None) or []:
        runs = getattr(run_handler, "_runs", None)
        if run_id is not None and isinstance(runs, dict) and type(run_handler).__module__.startswith("langfuse."):
            return runs.get(run_id)
    return None


def trace_id(run_handler) -> str | None:
    return getattr(run_handler, "last_trace_id", None) if run_handler is not None else None


def record_timings(
    settings: Settings, run_trace_id: str | None, timings: dict[str, int | float | None], guardrail: str | None = None
) -> None:
    """Attach an answer's timings to its trace as numeric scores, in milliseconds, with any other
    numeric scores (the guardrail's grounding scores) and the guardrail's decision for the turn
    as the categorical `guardrail` score.

    Scores can be filtered and charted in Langfuse, which is what the Phase 6
    latency targets are checked against. Never raises.
    """
    if run_trace_id is None or not settings.tracing_enabled():
        return
    try:
        lf = client(settings)
        for name, value in timings.items():
            if value is not None:
                lf.create_score(name=name, value=value, trace_id=run_trace_id, data_type="NUMERIC")
        if guardrail is not None:
            lf.create_score(name="guardrail", value=guardrail, trace_id=run_trace_id, data_type="CATEGORICAL")
    except Exception:  # noqa: BLE001 — tracing must never break answering
        log.warning("could not record timings on trace %s", run_trace_id, exc_info=True)
