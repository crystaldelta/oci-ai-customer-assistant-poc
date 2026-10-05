"""Where the API runs a turn (AD-21): in this process, or on AgentCore Runtime.

`AGENT_RUNTIME=in_process` calls the service directly. `agentcore` calls the AgentCore entry
point (`agentcore.py`) through `/invocations`: on AWS by the runtime's ARN
(`AGENTCORE_RUNTIME_ARN`, signed with the caller's IAM role), or by URL (`AGENTCORE_URL`, the
local `agentcore` Compose service). Either way the API gets the service's events and errors:
`service.TurnLimitReached` for a conversation at its limit, any other exception for a failed run.

The AgentCore session ID is made from the thread key, never from the signed ID, which is a
credential (AgentCore logs session IDs): the key's SHA-256 in hex, 64 characters, since
AgentCore wants 33 or more and a key is 22.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from typing import Protocol

from assistant import service as svc

log = logging.getLogger("ai-starter")

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"


class AgentRuntimeError(Exception):
    """AgentCore refused or failed a turn; the message holds its status and clean error."""


class AgentClient(Protocol):
    def answer(self, question: str, thread_key: str) -> dict: ...
    def astream_answer(self, question: str, thread_key: str) -> AsyncIterator[dict]: ...
    def health(self) -> dict: ...


def session_id(thread_key: str) -> str:
    """The AgentCore session ID for a thread key: the same key always gives the same session."""
    return hashlib.sha256(thread_key.encode()).hexdigest()


class InProcessAgent:
    """The service in this process (the local stack)."""

    def answer(self, question: str, thread_key: str) -> dict:
        return svc.answer(question=question, thread_id=thread_key)

    def astream_answer(self, question: str, thread_key: str) -> AsyncIterator[dict]:
        return svc.astream_answer(question=question, thread_id=thread_key)

    def health(self) -> dict:
        return svc.health()


@dataclass
class _Reply:
    status: int
    lines: Iterator[str]
    close: Callable[[], None]


def arriving_lines(body) -> Iterator[str]:
    """The lines of a botocore `StreamingBody`, each as soon as it arrives.

    Its own `iter_lines` reads 1,024-byte blocks and waits for each block to fill, so the
    `start` and `status` events (a few hundred bytes) were held back until the answer's tokens
    filled the first block: on AWS nothing reached the browser until the answer was written.
    urllib3's `read1` returns whatever has arrived. A body without it (a test's) uses `iter_lines`.
    """
    raw = getattr(body, "_raw_stream", None)
    if not hasattr(raw, "read1"):
        yield from (line.decode() for line in body.iter_lines())
        return
    pending = b""
    while chunk := raw.read1(8192):
        *lines, pending = (pending + chunk).split(b"\n")  # never splits a UTF-8 character
        for line in lines:
            yield line.removesuffix(b"\r").decode()
    if pending:
        yield pending.decode()


class AgentCoreAgent:
    """The AgentCore entry point, by ARN (boto3) or by URL (an `httpx.Client`)."""

    def __init__(self, invoke: Callable[[bytes, str], _Reply]) -> None:
        self._invoke = invoke

    @classmethod
    def by_arn(cls, arn: str, qualifier: str, region: str, client=None) -> "AgentCoreAgent":
        if client is None:
            import boto3

            client = boto3.client("bedrock-agentcore", region_name=region)

        def invoke(body: bytes, session: str) -> _Reply:
            from botocore.exceptions import ClientError

            try:
                res = client.invoke_agent_runtime(
                    agentRuntimeArn=arn,
                    qualifier=qualifier,
                    runtimeSessionId=session,
                    contentType="application/json",
                    accept="text/event-stream, application/json",
                    payload=body,
                )
            except ClientError as exc:
                error = exc.response.get("Error", {})
                status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 502
                return _Reply(status, iter([f"{error.get('Code')}: {error.get('Message')}"]), lambda: None)
            stream = res["response"]
            return _Reply(res.get("statusCode", 200), arriving_lines(stream), stream.close)

        return cls(invoke)

    @classmethod
    def by_url(cls, url: str, client=None) -> "AgentCoreAgent":
        if client is None:
            import httpx

            # A turn can pause between events while the model thinks; the connect timeout stays short.
            client = httpx.Client(timeout=httpx.Timeout(10.0, read=120.0))

        def invoke(body: bytes, session: str) -> _Reply:
            request = client.build_request(
                "POST",
                f"{url.rstrip('/')}/invocations",
                content=body,
                headers={"Content-Type": "application/json", SESSION_HEADER: session},
            )
            res = client.send(request, stream=True)
            return _Reply(res.status_code, res.iter_lines(), res.close)

        return cls(invoke)

    @staticmethod
    def _payload(question: str, stream: bool) -> bytes:
        return json.dumps({"question": question, "stream": stream}).encode()

    @staticmethod
    def _raise_for(status: int, body: str) -> None:
        # The entry point answers a conversation at its limit with 409 and the service's message;
        # check both, in case AgentCore reports the container's status as its own error.
        if status == 409 or svc.TURN_LIMIT_MESSAGE in body:
            raise svc.TurnLimitReached
        if status >= 300:
            raise AgentRuntimeError(f"AgentCore returned {status}: {body[:500]}")

    def answer(self, question: str, thread_key: str) -> dict:
        reply = self._invoke(self._payload(question, stream=False), session_id(thread_key))
        try:
            body = "\n".join(reply.lines)
        finally:
            reply.close()
        self._raise_for(reply.status, body)
        return json.loads(body)

    async def astream_answer(self, question: str, thread_key: str) -> AsyncIterator[dict]:
        # boto3 and the httpx client block, so each call and each read runs on a worker thread.
        reply = await asyncio.to_thread(self._invoke, self._payload(question, stream=True), session_id(thread_key))
        try:
            if reply.status >= 300:
                body = await asyncio.to_thread(lambda: "\n".join(reply.lines))
                self._raise_for(reply.status, body)
            while (line := await asyncio.to_thread(next, reply.lines, None)) is not None:
                if line.startswith("data: "):
                    yield json.loads(line[len("data: ") :])
        finally:
            await asyncio.to_thread(reply.close)

    def health(self) -> dict:
        # Cheap and local: the load balancer's readiness check must not start a session.
        return {"agent_runtime": "agentcore"}


def build(settings) -> AgentClient:
    """The client `AGENT_RUNTIME` selects."""
    if settings.agent_runtime == "in_process":
        return InProcessAgent()
    if settings.agentcore_runtime_arn:
        return AgentCoreAgent.by_arn(settings.agentcore_runtime_arn, settings.agentcore_qualifier, settings.aws_region)
    if settings.agentcore_url:
        return AgentCoreAgent.by_url(settings.agentcore_url)
    raise ValueError("AGENT_RUNTIME=agentcore needs AGENTCORE_RUNTIME_ARN or AGENTCORE_URL")
