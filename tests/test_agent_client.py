"""The API calling the agent through AgentCore's `/invocations` (AD-21).

The URL transport runs against the real entry point (`agentcore.app` in a TestClient) on the fake
seams, so a turn goes API → client → entry point → service as it does on AWS. The ARN transport
runs against a stub boto3 client. No AWS.
"""

import asyncio
import io
import json

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from starlette.testclient import TestClient as StarletteClient

from assistant import agent_client, agentcore, api, config, threads
from assistant import service as svc


@pytest.fixture(autouse=True)
def fresh_rate_limiter(monkeypatch):
    monkeypatch.setattr(api, "rate_limiter", api.RateLimiter(config.settings.rate_limit_per_minute))


@pytest.fixture
def sessions():
    """The session IDs the entry point received, in order."""
    return []


@pytest.fixture
def client(monkeypatch, fakes, sessions):
    """The API with its agent on AgentCore, by URL, served by the entry point in process."""
    runtime = StarletteClient(agentcore.app, base_url="http://agentcore")
    runtime.event_hooks = {"request": [lambda r: sessions.append(r.headers[agent_client.SESSION_HEADER])]}
    monkeypatch.setattr(api, "agent", agent_client.AgentCoreAgent.by_url("http://agentcore", client=runtime))
    return TestClient(api.app)


def _events(res) -> list[dict]:
    return [json.loads(line[len("data: ") :]) for line in res.text.splitlines() if line.startswith("data: ")]


# --------------------------------------------------------------- session IDs


def test_the_session_id_is_long_enough_for_agentcore_and_fixed_per_key():
    _, key = threads.issue()

    assert len(agent_client.session_id(key)) >= 33
    assert agent_client.session_id(key) == agent_client.session_id(key)
    assert key not in agent_client.session_id(key)


def test_each_turn_of_a_conversation_uses_the_session_made_from_its_key(client, sessions):
    first = client.post("/api/ask", json={"question": "How do I pay my bill?"}).json()
    client.post("/api/ask", json={"question": "And by card?", "thread_id": first["thread_id"]})

    key = threads.verify(first["thread_id"])
    assert sessions == [agent_client.session_id(key)] * 2
    assert first["thread_id"] not in sessions[0]  # the signed ID never leaves the API


# --------------------------------------------------------------- turns


def test_a_streamed_turn_reaches_the_caller_with_the_signed_id(client):
    res = client.post("/api/ask/stream", json={"question": "How do I pay my bill?"})

    events = _events(res)
    assert res.status_code == 200
    assert events[0]["type"] == "start"
    assert events[-1]["type"] == "done"
    assert threads.verify(events[0]["thread_id"]) is not None
    assert events[-1]["thread_id"] == events[0]["thread_id"]
    assert any(e["type"] == "token" for e in events)


def test_a_blocking_turn_returns_the_answer(client):
    res = client.post("/api/ask", json={"question": "How do I pay my bill?"})

    assert res.status_code == 200
    assert res.json()["answer"]
    assert threads.verify(res.json()["thread_id"]) is not None


def test_a_conversation_at_its_limit_is_a_409_through_agentcore(client, monkeypatch):
    monkeypatch.setattr(config.settings, "max_turns_per_thread", 1)
    first = client.post("/api/ask", json={"question": "How do I pay my bill?"}).json()

    blocking = client.post("/api/ask", json={"question": "Again?", "thread_id": first["thread_id"]})
    streamed = client.post("/api/ask/stream", json={"question": "Again?", "thread_id": first["thread_id"]})

    assert blocking.status_code == 409
    assert streamed.status_code == 409
    assert streamed.json()["detail"] == api.TURN_LIMIT_MESSAGE


def test_health_does_not_call_agentcore(client, sessions):
    res = client.get("/api/health")

    assert res.json() == {"agent_runtime": "agentcore"}
    assert sessions == []


# --------------------------------------------------------------- by ARN


class _Body:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.closed = False

    def iter_lines(self):
        return (line.encode() for line in self._lines)

    def close(self):
        self.closed = True


class _Boto:
    def __init__(self, reply=None, error: ClientError | None = None) -> None:
        self.reply, self.error, self.calls = reply, error, []

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.reply


def _client_error(status: int, message: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": "RuntimeClientError", "Message": message}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "InvokeAgentRuntime",
    )


async def _collect(stream) -> list[dict]:
    return [event async for event in stream]


def test_by_arn_streams_the_runtime_events_and_closes_the_body():
    body = _Body(['data: {"type": "start"}', "", 'data: {"type": "done"}', ""])
    boto = _Boto({"statusCode": 200, "response": body})
    agent = agent_client.AgentCoreAgent.by_arn("arn:runtime", "DEFAULT", "ap-southeast-2", client=boto)

    events = asyncio.run(_collect(agent.astream_answer("q", "key")))

    assert [e["type"] for e in events] == ["start", "done"]
    assert body.closed
    call = boto.calls[0]
    assert call["agentRuntimeArn"] == "arn:runtime"
    assert call["runtimeSessionId"] == agent_client.session_id("key")
    assert json.loads(call["payload"]) == {"question": "q", "stream": True}


class _Arriving(io.RawIOBase):
    """A socket that has received only `parts[:sent]`: reading past them is a test failure."""

    def __init__(self, parts: list[bytes]) -> None:
        self.parts, self.sent = parts, 1

    def readable(self):
        return True

    def readinto(self, buffer):
        if not self.parts:
            return 0
        assert self.sent > 0, "read past what has arrived"
        part = self.parts.pop(0)
        self.sent -= 1
        buffer[: len(part)] = part
        return len(part)


def test_by_arn_lines_are_yielded_as_they_arrive_not_by_the_kilobyte():
    from botocore.response import StreamingBody
    from urllib3.response import HTTPResponse

    start = b'data: {"type": "start"}\n\n'
    tokens = b'data: {"type": "token", "text": "' + b"x" * 2000 + b'"}\n\n'
    socket = _Arriving([start, tokens])
    body = StreamingBody(HTTPResponse(body=io.BufferedReader(socket), preload_content=False), None)

    lines = agent_client.arriving_lines(body)

    assert next(lines) == 'data: {"type": "start"}'  # botocore's iter_lines would wait for 1,024 bytes
    socket.sent = 1  # the tokens arrive
    assert [line for line in lines if line] == [tokens.decode().strip()]


def test_by_arn_a_turn_limit_refusal_is_turn_limit_reached():
    boto = _Boto(error=_client_error(409, svc.TURN_LIMIT_MESSAGE))
    agent = agent_client.AgentCoreAgent.by_arn("arn:runtime", "DEFAULT", "ap-southeast-2", client=boto)

    with pytest.raises(svc.TurnLimitReached):
        agent.answer("q", "key")


def test_by_arn_any_other_refusal_is_an_error():
    boto = _Boto(error=_client_error(403, "not authorised"))
    agent = agent_client.AgentCoreAgent.by_arn("arn:runtime", "DEFAULT", "ap-southeast-2", client=boto)

    with pytest.raises(agent_client.AgentRuntimeError, match="403"):
        agent.answer("q", "key")


# --------------------------------------------------------------- settings


def test_the_default_runs_the_service_in_process():
    assert isinstance(agent_client.build(config.settings), agent_client.InProcessAgent)


def test_agentcore_without_a_target_is_refused(monkeypatch):
    monkeypatch.setattr(config.settings, "agent_runtime", "agentcore")

    with pytest.raises(ValueError, match="AGENTCORE_RUNTIME_ARN or AGENTCORE_URL"):
        agent_client.build(config.settings)


# --------------------------------------------------------------- client address


@pytest.mark.parametrize(
    ("header", "address"),
    [("203.0.113.7:46532", "203.0.113.7"), ("2001:db8::7:46532", "2001:db8::7")],
)
def test_the_rate_limit_keys_on_the_proxy_header_when_set(monkeypatch, header, address):
    monkeypatch.setattr(config.settings, "client_address_header", "CloudFront-Viewer-Address")
    request = _request({"cloudfront-viewer-address": header})

    assert api.client_address(request) == address


def test_without_the_setting_the_header_is_ignored():
    request = _request({"cloudfront-viewer-address": "203.0.113.7:46532"})

    assert api.client_address(request) == "10.0.0.1"


def _request(headers: dict[str, str]):
    from starlette.requests import Request

    raw = [(k.encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": ("10.0.0.1", 1234)})
