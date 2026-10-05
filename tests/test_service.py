"""Unit tests for the pure logic in assistant.service.

No network, no Ollama, no embedding model: ID generation, config wiring, the
singleton lock, the turn limit, the health guard and reasoning stripping.

Run from the project root:
    pytest -q
"""

import asyncio
import threading

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from assistant import config, tracing
from assistant import service as svc

# ------------------------------------------------------------------- config


def test_new_thread_id_is_unique():
    assert svc.new_thread_id() != svc.new_thread_id()


def test_build_config_wires_thread_id_and_session():
    run_config, handler = svc.build_config("thread-abc")

    assert run_config["configurable"]["thread_id"] == "thread-abc"
    assert run_config["metadata"]["langfuse_session_id"] == "thread-abc"


def test_build_config_merges_extra_metadata():
    run_config, _ = svc.build_config("t1", metadata={"caller": "test"})

    assert run_config["metadata"]["caller"] == "test"
    assert run_config["metadata"]["langfuse_session_id"] == "t1"


def test_build_config_has_no_tracing_without_langfuse_keys():
    run_config, handler = svc.build_config("t1")

    assert handler is None
    assert "callbacks" not in run_config


def test_build_config_attaches_langfuse_when_keys_are_set(monkeypatch):
    monkeypatch.setattr(config.settings, "langfuse_public_key", "pk-lf-test")
    monkeypatch.setattr(config.settings, "langfuse_secret_key", "sk-lf-test")
    monkeypatch.setattr(tracing, "_client", None)
    run_config, handler = svc.build_config("t1")

    assert handler is not None
    assert run_config["callbacks"] == [handler]
    assert tracing._client._mask is tracing.mask_pii  # traces are masked


# --------------------------------------------------------------- singletons
#
# get_agent() holds the module lock and calls get_components(), which takes
# the same lock. With a plain threading.Lock that never returns, so every
# question hangs while the rest of the suite stays green. Building the agent
# needs no network (ChatOllama is only constructed), so this is a cheap guard on
# real behaviour.


def test_get_agent_returns_without_deadlocking():
    agent = _with_timeout(svc.get_agent, "get_agent() deadlocked")

    assert agent is not None


def test_get_agent_is_cached():
    first = _with_timeout(svc.get_agent, "get_agent() deadlocked")
    again = _with_timeout(svc.get_agent, "get_agent() deadlocked")

    assert first is again


def test_nested_singleton_getters_do_not_deadlock():
    # Directly assert the lock is reentrant, so a future getter that nests
    # inside another fails loudly here rather than hanging in production.
    def nested():
        with svc._lock, svc._lock:
            return True

    assert _with_timeout(nested, "module lock is not reentrant") is True


def _with_timeout(fn, message: str, seconds: float = 60.0):
    """Run `fn` on a worker thread and fail the test if it blocks.

    A deadlock would otherwise hang the whole suite with no diagnostic.
    """
    box: dict = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised on the main thread
            box["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)

    assert not worker.is_alive(), f"{message} (still running after {seconds}s)"
    if "error" in box:
        raise box["error"]
    return box["value"]


# --------------------------------------------------------------- turn limit
#
# Turns are counted from the thread's memory, so these write to the real
# checkpointer (in-process MemorySaver, see conftest.py) without running the model.


def _seed_turns(thread_id: str, n: int) -> None:
    messages = []
    for i in range(n):
        messages += [HumanMessage(content=f"q{i}"), AIMessage(content=f"a{i}")]
    svc.get_agent().update_state(
        {"configurable": {"thread_id": thread_id}}, {"messages": messages}, as_node=svc.GENERATE
    )


def test_turns_used_counts_questions_not_messages():
    thread_id = svc.new_thread_id()
    assert svc.turns_used(thread_id) == 0

    _seed_turns(thread_id, 3)

    assert svc.turns_used(thread_id) == 3


def test_answer_refuses_a_thread_at_the_turn_limit(monkeypatch):
    monkeypatch.setattr(config.settings, "max_turns_per_thread", 2)
    thread_id = svc.new_thread_id()
    _seed_turns(thread_id, 2)

    with pytest.raises(svc.TurnLimitReached):
        svc.answer("one more?", thread_id=thread_id)


def test_the_stream_refuses_before_its_first_event(monkeypatch):
    monkeypatch.setattr(config.settings, "max_turns_per_thread", 1)
    thread_id = svc.new_thread_id()
    _seed_turns(thread_id, 1)

    with pytest.raises(svc.TurnLimitReached):
        asyncio.run(anext(svc.astream_answer("one more?", thread_id=thread_id)))


# ------------------------------------------------------------------- health


def test_health_reports_shape_and_never_raises(monkeypatch):
    # Point at dead ports: health must come back with the *_ok flags False and
    # error strings, not blow up.
    monkeypatch.setattr(config.settings, "qdrant_url", "http://127.0.0.1:1")
    monkeypatch.setattr(config.settings, "ollama_base_url", "http://127.0.0.1:1")
    status = svc.health()

    for key in ("ollama_ok", "qdrant_ok", "collection", "embedding_model", "llm_model", "points", "memory", "prompts"):
        assert key in status
    assert status["ollama_ok"] is False
    assert status["qdrant_ok"] is False
    assert len(status["errors"]) == 2
    # /api/health is public: the errors name the dependency, never the exception or its address.
    assert status["errors"] == ["ollama: unreachable", "qdrant: unreachable"]


def test_health_reports_where_the_prompts_come_from(fakes, monkeypatch):
    monkeypatch.setattr(config.settings, "qdrant_url", "http://127.0.0.1:1")
    monkeypatch.setattr(config.settings, "ollama_base_url", "http://127.0.0.1:1")

    assert svc.health()["prompts"] == {"source": "fake"}


# ------------------------------------------------- reasoning / <think> stripping


def test_strip_reasoning_leaves_plain_text_untouched():
    assert svc.strip_reasoning("You can pay by BPAY.") == "You can pay by BPAY."


def test_strip_reasoning_removes_a_think_block():
    assert svc.strip_reasoning("<think>hmm let me see</think>Use BPAY.") == "Use BPAY."


def test_strip_reasoning_removes_multiple_blocks():
    out = svc.strip_reasoning("<think>a</think>One. <think>b</think>Two.")
    assert "a" not in out and "b" not in out
    assert "One." in out and "Two." in out


def test_strip_reasoning_discards_an_unterminated_block():
    # Ran out of tokens mid-thought: better an empty answer than raw reasoning.
    assert svc.strip_reasoning("<think>I was still thinking when I ran out") == ""


def test_strip_reasoning_keeps_text_before_the_block():
    assert svc.strip_reasoning("Answer. <think>aside</think>") == "Answer."


def _stream(chunks):
    f = svc.ReasoningFilter()
    return "".join(f.feed(c) for c in chunks) + f.flush()


def test_filter_passes_plain_streamed_text_through():
    assert _stream(["You ", "can ", "use BPAY."]) == "You can use BPAY."


def test_filter_removes_a_think_block_from_a_stream():
    assert _stream(["<think>", "reasoning here", "</think>", "Answer."]) == "Answer."


def test_filter_handles_a_tag_split_across_chunks():
    # The realistic failure: "<thi" arrives in one chunk and "nk>" in the next.
    assert _stream(["<thi", "nk>", "secret", "</thi", "nk>", "Answer."]) == "Answer."


def test_filter_handles_one_character_at_a_time():
    assert _stream(list("<think>hidden</think>Visible.")) == "Visible."


def test_filter_emits_nothing_for_an_unterminated_stream():
    assert _stream(["<think>", "still thinking"]) == ""


def test_filter_does_not_swallow_a_lone_less_than():
    assert _stream(["a < b and c > d"]) == "a < b and c > d"


def test_filter_flush_releases_held_back_tail():
    # A trailing partial-tag lookalike must still be emitted, not lost.
    assert _stream(["Answer", "<thi"]) == "Answer<thi"
