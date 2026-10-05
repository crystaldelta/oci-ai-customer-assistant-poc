"""The AgentCore Runtime entry point: the contract (`/ping`, `/invocations` on the
session header), and the same turns, errors and events as the API. No AWS.
"""

import json

import pytest
from starlette.testclient import TestClient

from assistant import agentcore
from assistant import service as svc

SESSION = {"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": "session-0123456789abcdef0123456789abcdef"}


@pytest.fixture
def client():
    with TestClient(agentcore.app) as c:
        yield c


def _events(res) -> list[dict]:
    return [json.loads(line[len("data: ") :]) for line in res.text.splitlines() if line.startswith("data: ")]


def test_ping_answers_healthy(client):
    res = client.get("/ping")

    assert res.status_code == 200
    assert res.json()["status"] == "Healthy"


def test_an_invocation_streams_the_service_events_on_the_session(client, fakes):
    res = client.post("/invocations", json={"question": "How do I pay?"}, headers=SESSION)

    events = _events(res)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert [e["type"] for e in events][0] == "start"
    assert events[-1]["type"] == "done"
    assert events[-1]["thread_id"] == SESSION["X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"]
    assert any(e["type"] == "token" for e in events)


def test_the_session_is_the_conversation(client, fakes):
    client.post("/invocations", json={"question": "How do I pay?"}, headers=SESSION)
    client.post("/invocations", json={"question": "And by card?"}, headers=SESSION)

    assert len([m for m in fakes.llm.calls[-1] if m.type == "human"]) == 2


def test_a_blocking_invocation_returns_the_answer(client, fakes):
    res = client.post("/invocations", json={"question": "How do I pay?", "stream": False}, headers=SESSION)

    assert res.status_code == 200
    assert res.json()["answer"]
    assert res.json()["thread_id"] == SESSION["X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"]


@pytest.mark.parametrize(
    "payload",
    [{}, {"question": ""}, {"question": "q" * 3000}, {"question": "q", "thread_id": "x"}],
    ids=["no-question", "empty", "too-long", "unknown-field"],
)
def test_an_invalid_payload_is_a_400(client, payload):
    res = client.post("/invocations", json=payload, headers=SESSION)

    assert res.status_code == 400
    assert res.json()["error"].startswith(agentcore.INVALID_REQUEST_MESSAGE)


def test_an_invalid_payload_error_does_not_repeat_the_input(client):
    res = client.post("/invocations", json={"question": "x" * 5000 + "my-secret"}, headers=SESSION)

    assert res.status_code == 400
    assert "my-secret" not in res.text


def test_no_session_header_is_a_400(client):
    res = client.post("/invocations", json={"question": "q"})

    assert res.status_code == 400
    assert res.json()["error"] == agentcore.NO_SESSION_MESSAGE


@pytest.mark.parametrize("stream", [True, False])
def test_the_turn_limit_is_a_409(client, monkeypatch, stream):
    def limited(*_, **__):
        raise svc.TurnLimitReached("k")

    async def limited_stream(**_):
        raise svc.TurnLimitReached("k")
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(svc, "answer", limited)
    monkeypatch.setattr(svc, "astream_answer", limited_stream)
    res = client.post("/invocations", json={"question": "q", "stream": stream}, headers=SESSION)

    assert res.status_code == 409
    assert res.json()["error"] == svc.TURN_LIMIT_MESSAGE


@pytest.mark.parametrize("stream", [True, False])
def test_a_failed_run_returns_a_generic_message(client, monkeypatch, stream):
    def boom(*_, **__):
        raise RuntimeError("provider payload with internals")

    async def boom_stream(**_):
        raise RuntimeError("provider payload with internals")
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(svc, "answer", boom)
    monkeypatch.setattr(svc, "astream_answer", boom_stream)
    res = client.post("/invocations", json={"question": "q", "stream": stream}, headers=SESSION)

    assert res.status_code == 502
    assert res.json()["error"] == svc.ERROR_MESSAGE
    assert "internals" not in res.text


def test_a_stream_that_fails_midway_ends_with_a_generic_error_event(client, monkeypatch):
    async def breaks(question, thread_id=None, metadata=None):
        yield {"type": "start", "thread_id": thread_id, "model": "m"}
        raise RuntimeError("provider payload with internals")

    monkeypatch.setattr(svc, "astream_answer", breaks)
    res = client.post("/invocations", json={"question": "q"}, headers=SESSION)

    assert res.status_code == 200
    assert _events(res)[-1] == {"type": "error", "message": svc.ERROR_MESSAGE}
    assert "internals" not in res.text
