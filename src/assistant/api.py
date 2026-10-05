"""HTTP API over `service`.

Run it:
    uvicorn assistant.api:app --host 0.0.0.0 --port 8000

Endpoints
    GET  /api/health          dependency and corpus status
    POST /api/ask             answer a question (JSON, blocking)
    POST /api/ask/stream      same run, streamed as server-sent events

Callers are anonymous. A conversation is identified by a server-issued, signed
`thread_id` (see `threads.py`): omit it to start a conversation, then send back
the one from the response. An unsigned or forged `thread_id` is refused.

A turn runs in this process or on AgentCore Runtime, by `AGENT_RUNTIME`
(`agent_client.py`, AD-21); the endpoints are the same either way.
"""

import json
import logging
import threading
import time
from collections import deque

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from assistant import agent_client, config, threads
from assistant import service as svc

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ai-starter")

app = FastAPI(title="ai-starter API", version="0.1.0")

ERROR_MESSAGE = svc.ERROR_MESSAGE
TURN_LIMIT_MESSAGE = svc.TURN_LIMIT_MESSAGE
INVALID_THREAD_MESSAGE = "Unknown or invalid thread_id. Omit it to start a new conversation."
RATE_LIMIT_MESSAGE = "Too many requests. Please wait a moment and try again."


class AskRequest(BaseModel):
    # Unknown fields are an error, not ignored: a misspelt `thread_id` would
    # otherwise start a new conversation on every turn without any sign why.
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=config.settings.max_question_chars)
    thread_id: str | None = Field(default=None, max_length=128)


class RateLimiter:
    """Sliding one-minute window of requests per client.

    In-process, so it is per replica and resets on restart. It keys on the
    socket peer address, or on `CLIENT_ADDRESS_HEADER` when a proxy the caller
    cannot get around sets it (AD-21). Never `X-Forwarded-For`, which any caller
    can set; behind a proxy without the header, all callers share one bucket.
    """

    def __init__(self, per_minute: int, clock=time.monotonic) -> None:
        self.per_minute = per_minute
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def retry_after(self, client: str) -> int | None:
        """Record a request. Returns None if allowed, else seconds to wait."""
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(client, deque())
            while hits and hits[0] <= now - 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return max(1, int(hits[0] + 60 - now) + 1)
            hits.append(now)
            # Drop idle clients so the table does not grow without bound.
            if len(self._hits) > 10_000:
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > now - 60}
            return None


rate_limiter = RateLimiter(config.settings.rate_limit_per_minute)
agent = agent_client.build(config.settings)


def client_address(request: Request) -> str:
    if config.settings.client_address_header:
        # CloudFront's `CloudFront-Viewer-Address` is `<ip>:<port>`, IPv6 included; drop the port.
        value = request.headers.get(config.settings.client_address_header, "")
        if value:
            return value.rpartition(":")[0] or value
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request) -> None:
    client = client_address(request)
    wait = rate_limiter.retry_after(client)
    if wait is not None:
        raise HTTPException(status_code=429, detail=RATE_LIMIT_MESSAGE, headers={"Retry-After": str(wait)})


def resolve_thread(req: AskRequest) -> tuple[str, str]:
    """Return `(signed_id, key)`: a new thread, or the caller's if its signature holds."""
    if req.thread_id is None:
        return threads.issue()
    key = threads.verify(req.thread_id)
    if key is None:
        raise HTTPException(status_code=400, detail=INVALID_THREAD_MESSAGE)
    return req.thread_id, key


def _to_caller(event: dict, signed_id: str) -> dict:
    """The service works with the key; the caller only ever sees the signed ID."""
    return {**event, "thread_id": signed_id} if "thread_id" in event else event


@app.get("/api/health")
def health():
    return agent.health()


@app.post("/api/ask", dependencies=[Depends(rate_limit)])
def ask(req: AskRequest):
    signed_id, key = resolve_thread(req)
    try:
        return _to_caller(agent.answer(req.question, key), signed_id)
    except svc.TurnLimitReached as exc:
        raise HTTPException(status_code=409, detail=TURN_LIMIT_MESSAGE) from exc
    except Exception as exc:  # noqa: BLE001 — surface a clean error to the caller
        log.exception("ask failed (thread %s)", key)
        raise HTTPException(status_code=502, detail=ERROR_MESSAGE) from exc


@app.post("/api/ask/stream", dependencies=[Depends(rate_limit)])
async def ask_stream(req: AskRequest):
    signed_id, key = resolve_thread(req)
    stream = agent.astream_answer(req.question, key)

    # Pull the first event before committing to a 200, so a refusal (the turn
    # limit) is an HTTP status rather than an error inside a successful stream.
    try:
        first = await anext(stream)
    except svc.TurnLimitReached as exc:
        raise HTTPException(status_code=409, detail=TURN_LIMIT_MESSAGE) from exc
    except Exception as exc:  # noqa: BLE001 — surface a clean error to the caller
        log.exception("stream failed to start (thread %s)", key)
        raise HTTPException(status_code=502, detail=ERROR_MESSAGE) from exc

    async def events():
        try:
            yield f"data: {json.dumps(_to_caller(first, signed_id))}\n\n"
            async for event in stream:
                yield f"data: {json.dumps(_to_caller(event, signed_id))}\n\n"
        except Exception:  # noqa: BLE001 — the stream owns its errors
            log.exception("stream failed (thread %s)", key)
            yield f"data: {json.dumps({'type': 'error', 'message': ERROR_MESSAGE})}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # don't let a proxy buffer the stream
        },
    )
