"""In-memory fakes for each seam, so the service runs end to end with no
provider: no Ollama, Qdrant, Langfuse or network.
"""

from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

from assistant import prompts
from assistant import service as svc
from assistant.guardrail import ALLOWED, GuardrailResult
from assistant.retrieval import Passage

# The fake model's answer. The <think> block checks that ReasoningFilter runs.
ANSWER = "<think>checking the page</think>You can pay by BPAY or direct debit [1]."
VISIBLE_ANSWER = "You can pay by BPAY or direct debit [1]."

REWRITE_PROMPT = "FAKE REWRITE"

PASSAGES = [
    Passage(source="Ways to pay your bill", content="Pay by BPAY or direct debit.", url="https://example.invalid/pay"),
    Passage(source="Moving house", content="Give three business days' notice."),
]


class FakeChatModel(BaseChatModel):
    """A chat model that gives `answer` (`ANSWER` by default). Every call's messages are
    recorded in `calls`, except a follow-up rewrite's, which go to `rewrites`; it answers that
    with `rewrite`, or the latest message as it is."""

    calls: list = Field(default_factory=list)
    rewrites: list = Field(default_factory=list)
    answer: str = ANSWER
    rewrite: str | None = None

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _reply(self, messages) -> AIMessage:
        if messages[0].content == REWRITE_PROMPT:
            self.rewrites.append(list(messages))
            latest = messages[-1].content.rpartition("Latest message: ")[2]
            return AIMessage(content=self.rewrite if self.rewrite is not None else latest)
        self.calls.append(list(messages))
        return AIMessage(content=self.answer)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        reply = self._reply(messages)
        # Small chunks, so the <think> tags straddle chunk boundaries.
        for i in range(0, len(reply.content), 5):
            yield ChatGenerationChunk(message=AIMessageChunk(content=reply.content[i : i + 5]))


class FakeBlocksModel(FakeChatModel):
    """Replies as Bedrock's Converse does: a list of content blocks, reasoning first."""

    def _blocks(self, messages) -> list[dict]:
        self.calls.append(list(messages))
        return [
            {"type": "reasoning_content", "reasoning_content": {"text": "The customer wants to pay."}, "index": 0},
            {"type": "text", "text": "Pay by BPAY [1].", "index": 1},
        ]

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self._blocks(messages)))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        for block in self._blocks(messages):
            yield ChatGenerationChunk(message=AIMessageChunk(content=[block]))


@dataclass
class FakeRetriever:
    passages: list[Passage] = field(default_factory=lambda: list(PASSAGES))
    queries: list[str] = field(default_factory=list)

    def search(self, query: str) -> list[Passage]:
        self.queries.append(query)
        return list(self.passages)


@dataclass
class FakeGuardrail:
    """Blocks any text containing `block`, and masks `mask` as "[masked]"; records every check,
    and the grounding source and query of each in `grounded`. Each check returns `scores`."""

    block: str | None = None
    mask: str | None = None
    message: str = "Sorry, I can't help with that."
    scores: dict = field(default_factory=dict)
    checks: list[tuple[str, str]] = field(default_factory=list)
    grounded: list[tuple[str | None, str | None]] = field(default_factory=list)

    def check(self, text, *, source, grounding=None, query=None):
        self.checks.append((source, text))
        self.grounded.append((grounding, query))
        if self.block and self.block in text:
            return GuardrailResult(allowed=False, message=self.message, findings=("content:HATE",))
        if self.mask and self.mask in text:
            masked = text.replace(self.mask, "[masked]")
            return GuardrailResult(allowed=True, text=masked, findings=("pii:CREDIT_DEBIT_CARD_NUMBER",))
        return GuardrailResult(allowed=True, scores=dict(self.scores)) if self.scores else ALLOWED


@dataclass
class FakePrompts:
    texts: dict[str, str] = field(
        default_factory=lambda: {
            "system": "FAKE SYSTEM PROMPT",
            "emergency": "FAKE CALL 000",
            "decline": "FAKE DECLINE",
            "handoff": "FAKE HANDOFF",
            "rewrite": REWRITE_PROMPT,
        }
    )

    # Served as if from Langfuse: the version, and the object a trace links to, by name.
    versions: dict[str, int] = field(default_factory=dict)
    refs: dict[str, object] = field(default_factory=dict)

    def get_prompt(self, name: str) -> prompts.Prompt:
        return prompts.Prompt(name, self.texts[name], self.versions.get(name), self.refs.get(name))

    def get(self, name: str) -> str:
        return self.texts[name]

    def status(self) -> dict:
        return {"source": "fake"}


def fake_components(**overrides) -> svc.Components:
    parts = {
        "llm": FakeChatModel(),
        "model": "fake-model",
        "retriever": FakeRetriever(),
        "checkpointer": MemorySaver(),
        "guardrail": FakeGuardrail(),
        "prompts": FakePrompts(),
    }
    return svc.Components(**(parts | overrides))


def publish_index(store, tmp_path, texts: list[str], content_id: str, embedding_model: str = "fake") -> dict:
    """Publish a small embedded index of `texts` (sources "p0", "p1", ...) to an index store, as
    `publish.py` does, with one content page beside it. Returns the `LATEST.json` written."""
    from langchain_core.embeddings import DeterministicFakeEmbedding
    from langchain_qdrant import QdrantVectorStore

    content_dir = tmp_path / f"content-{content_id}"
    content_dir.mkdir(parents=True)
    (content_dir / "p.md").write_text("---\ntitle: P\nurl: https://example.invalid/p\n---\n\nText.\n")
    index_dir = tmp_path / f"index-{content_id}"
    written = QdrantVectorStore.from_texts(
        texts,
        DeterministicFakeEmbedding(size=16),
        metadatas=[{"source": f"p{i}"} for i in range(len(texts))],
        path=str(index_dir),
        collection_name="content",
    )
    written.client.close()
    meta = {
        "index": f"{content_id}-20261001T000000Z",
        "content_id": content_id,
        "embedding_model": embedding_model,
        "collection": "content",
        "points": len(texts),
        "pages": 1,
    }
    return store.publish(content_dir, index_dir, meta)
