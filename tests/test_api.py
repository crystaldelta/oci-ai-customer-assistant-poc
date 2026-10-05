"""Tests for the FastAPI layer, with the answering core stubbed out.

No Ollama, no Qdrant, no embedding model.
"""

import json

import pytest
from fastapi.testclient import TestClient

from assistant import api, config, threads


@pytest.fixture(autouse=True)
def fresh_rate_limiter(monkeypatch):
    # The limiter is module state; without this, requests leak between tests.
    monkeypatch.setattr(api, "rate_limiter", api.RateLimiter(config.settings.rate_limit_per_minute))


@pytest.fixture
def client(monkeypatch):
    """TestClient with the answering core replaced by a recorder."""
    calls = []

    def fake_answer(question, thread_id=None, metadata=None):
        calls.append({"question": question, "thread_id": thread_id})
        return {"answer": "stub", "sources": [], "thread_id": thread_id, "trace_id": None, "model": "m"}

    async def fake_stream(question, thread_id=None, metadata=None):
        calls.append({"question": question, "thread_id": thread_id})
        yield {"type": "start", "thread_id": thread_id, "model": "m"}
        yield {"type": "token", "text": "stub"}
        yield {"type": "done", "answer": "stub", "sources": [], "thread_id": thread_id}

    monkeypatch.setattr(api.svc, "answer", fake_answer)
    monkeypatch.setattr(api.svc, "astream_answer", fake_stream)
    c = TestClient(api.app)
    c.calls = calls  # type: ignore[attr-defined]
    return c


def _events(res) -> list[dict]:
    return [json.loads(line[len("data: ") :]) for line in res.text.splitlines() if line.startswith("data: ")]


# --------------------------------------------------------------- thread IDs


def test_a_signed_thread_id_is_issued_when_the_client_sends_none(client):
    res = client.post("/api/ask", json={"question": "q"})

    assert res.status_code == 200
    signed_id = res.json()["thread_id"]
    assert threads.verify(signed_id) is not None


def test_the_service_gets_the_key_and_the_caller_gets_the_signed_id(client):
    res = client.post("/api/ask", json={"question": "q"})

    signed_id = res.json()["thread_id"]
    assert client.calls[0]["thread_id"] == threads.verify(signed_id)  # type: ignore[attr-defined]
    assert client.calls[0]["thread_id"] != signed_id  # type: ignore[attr-defined]


def test_a_signed_thread_id_round_trips(client):
    signed_id = client.post("/api/ask", json={"question": "q"}).json()["thread_id"]

    res = client.post("/api/ask", json={"question": "again", "thread_id": signed_id})

    assert res.status_code == 200
    assert res.json()["thread_id"] == signed_id
    assert client.calls[0]["thread_id"] == client.calls[1]["thread_id"]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "thread_id",
    [
        "client-abc",  # chosen by the client
        "8c1f4d0e-6a2b-4f7e-9d3c-2b1a0e9f8d7c",  # a plain UUID, the old format
        "somekey.forgedsignature",  # right shape, wrong signature
        ".",
    ],
)
def test_an_unsigned_or_forged_thread_id_is_refused(client, thread_id):
    res = client.post("/api/ask", json={"question": "q", "thread_id": thread_id})

    assert res.status_code == 400
    assert res.json()["detail"] == api.INVALID_THREAD_MESSAGE
    assert client.calls == []  # type: ignore[attr-defined]


def test_a_signature_for_another_key_is_refused(client):
    signed_a, _ = threads.issue()
    _, key_b = threads.issue()
    spliced = f"{key_b}.{signed_a.partition('.')[2]}"

    assert client.post("/api/ask", json={"question": "q", "thread_id": spliced}).status_code == 400


def test_the_stream_issues_a_signed_thread_id_in_start_and_done(client):
    res = client.post("/api/ask/stream", json={"question": "q"})

    events = _events(res)
    start, done = events[0], events[-1]
    assert start["type"] == "start"
    assert done["type"] == "done"
    assert start["thread_id"] == done["thread_id"]
    assert threads.verify(start["thread_id"]) == client.calls[0]["thread_id"]  # type: ignore[attr-defined]


def test_the_stream_refuses_a_forged_thread_id(client):
    res = client.post("/api/ask/stream", json={"question": "q", "thread_id": "client-abc"})

    assert res.status_code == 400


# ------------------------------------------------------------ API contract


def test_unknown_fields_are_rejected(client):
    # The old camelCase name must fail loudly, not silently start a new thread.
    res = client.post("/api/ask", json={"question": "q", "threadId": "x"})

    assert res.status_code == 422
    assert client.calls == []  # type: ignore[attr-defined]


def test_unknown_fields_are_rejected_on_the_stream(client):
    assert client.post("/api/ask/stream", json={"question": "q", "extra": 1}).status_code == 422


def test_an_empty_question_is_rejected(client):
    assert client.post("/api/ask", json={"question": ""}).status_code == 422


def test_an_overlong_question_is_rejected(client):
    res = client.post("/api/ask", json={"question": "x" * (config.settings.max_question_chars + 1)})

    assert res.status_code == 422
    assert client.calls == []  # type: ignore[attr-defined]


def test_a_question_at_the_limit_is_accepted(client):
    assert client.post("/api/ask", json={"question": "x" * config.settings.max_question_chars}).status_code == 200


# ------------------------------------------------------------------ limits


def test_the_rate_limit_triggers_with_retry_after(client, monkeypatch):
    monkeypatch.setattr(api, "rate_limiter", api.RateLimiter(per_minute=2))

    assert client.post("/api/ask", json={"question": "q"}).status_code == 200
    assert client.post("/api/ask/stream", json={"question": "q"}).status_code == 200
    res = client.post("/api/ask", json={"question": "q"})

    assert res.status_code == 429
    assert res.json()["detail"] == api.RATE_LIMIT_MESSAGE
    assert int(res.headers["Retry-After"]) >= 1
    assert len(client.calls) == 2  # type: ignore[attr-defined]


def test_the_rate_limit_window_slides():
    now = [1000.0]
    limiter = api.RateLimiter(per_minute=2, clock=lambda: now[0])

    assert limiter.retry_after("a") is None
    assert limiter.retry_after("a") is None
    assert limiter.retry_after("a") is not None
    assert limiter.retry_after("b") is None  # clients have separate buckets

    now[0] += 61
    assert limiter.retry_after("a") is None


def test_the_turn_limit_is_a_409(client, monkeypatch):
    def limited(**_):
        raise api.svc.TurnLimitReached("k")

    monkeypatch.setattr(api.svc, "answer", limited)
    res = client.post("/api/ask", json={"question": "q"})

    assert res.status_code == 409
    assert res.json()["detail"] == api.TURN_LIMIT_MESSAGE


def test_the_turn_limit_is_a_409_on_the_stream_not_an_error_event(client, monkeypatch):
    async def limited(**_):
        raise api.svc.TurnLimitReached("k")
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(api.svc, "astream_answer", limited)
    res = client.post("/api/ask/stream", json={"question": "q"})

    assert res.status_code == 409
    assert res.json()["detail"] == api.TURN_LIMIT_MESSAGE


# ------------------------------------------------------------------ errors


def test_a_failed_run_returns_a_generic_message(monkeypatch):
    def boom(**_):
        raise RuntimeError("provider payload with internals")

    monkeypatch.setattr(api.svc, "answer", boom)
    res = TestClient(api.app).post("/api/ask", json={"question": "q"})

    assert res.status_code == 502
    assert res.json()["detail"] == api.ERROR_MESSAGE
    assert "internals" not in res.text


def test_a_stream_that_fails_midway_ends_with_a_generic_error_event(client, monkeypatch):
    async def breaks(question, thread_id=None, metadata=None):
        yield {"type": "start", "thread_id": thread_id, "model": "m"}
        raise RuntimeError("provider payload with internals")

    monkeypatch.setattr(api.svc, "astream_answer", breaks)
    res = client.post("/api/ask/stream", json={"question": "q"})

    events = _events(res)
    assert res.status_code == 200
    assert events[-1] == {"type": "error", "message": api.ERROR_MESSAGE}
    assert "internals" not in res.text
