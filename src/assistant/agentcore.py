"""AgentCore Runtime entry point over `service` (AD-20).

AgentCore runs this module's image and calls it on port 8080:

    GET  /ping          health, answered by the SDK
    POST /invocations   one turn: {"question": ..., "stream": true}

Run it:
    python -m assistant.agentcore

The conversation is the AgentCore session: the runtime passes the caller's
session ID in the `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` header, and it
is the service's thread key (memory, turn limit, Langfuse session). The caller
is trusted (AgentCore authenticates it with IAM), so the ID is not signed here.
A caller holding a signed thread ID (`threads.py`) must send a session ID made
from its key, never the signed ID: the runtime logs the session ID.

With `"stream": true` (the default) the response is server-sent events, the
service's `start` / `status` / `sources` / `token` / `replace` / `done` /
`error` events as `data: {...}` lines, the same as `/api/ask/stream`.
Otherwise it is the JSON of `service.answer`.
"""

import asyncio
import logging

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.responses import JSONResponse

from assistant import config
from assistant import service as svc

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ai-starter")

PORT = 8080  # the AgentCore Runtime contract
INVALID_REQUEST_MESSAGE = "Invalid request."
NO_SESSION_MESSAGE = "The session ID header is missing."

app = BedrockAgentCoreApp()


class Invocation(BaseModel):
    """The payload of one turn, as `api.AskRequest` without the thread ID (the session is the thread)."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=config.settings.max_question_chars)
    stream: bool = True


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@app.entrypoint
async def invoke(payload, context):
    """Answer one turn. Errors are clean messages: the SDK's own would show the exception."""
    try:
        req = Invocation.model_validate(payload)
    except ValidationError as exc:
        # Field names and reasons only: the input itself may hold personal details.
        return _error(400, f"{INVALID_REQUEST_MESSAGE} {', '.join(str(e['loc'][0]) for e in exc.errors())}")
    thread_key = context.session_id
    if not thread_key:
        return _error(400, NO_SESSION_MESSAGE)

    if not req.stream:
        try:
            return await asyncio.to_thread(svc.answer, req.question, thread_id=thread_key)
        except svc.TurnLimitReached:
            return _error(409, svc.TURN_LIMIT_MESSAGE)
        except Exception:  # noqa: BLE001 — surface a clean error to the caller
            log.exception("invocation failed (thread %s)", thread_key)
            return _error(502, svc.ERROR_MESSAGE)

    stream = svc.astream_answer(question=req.question, thread_id=thread_key)
    # Pull the first event before committing to a 200, so a refusal (the turn limit) is an HTTP
    # status rather than an error inside a successful stream, as in `api.py`.
    try:
        first = await anext(stream)
    except svc.TurnLimitReached:
        return _error(409, svc.TURN_LIMIT_MESSAGE)
    except Exception:  # noqa: BLE001 — surface a clean error to the caller
        log.exception("stream failed to start (thread %s)", thread_key)
        return _error(502, svc.ERROR_MESSAGE)
    return _events(first, stream, thread_key)


async def _events(first: dict, stream, thread_key: str):
    try:
        yield first
        async for event in stream:
            yield event
    except Exception:  # noqa: BLE001 — the stream owns its errors
        log.exception("stream failed (thread %s)", thread_key)
        yield {"type": "error", "message": svc.ERROR_MESSAGE}


if __name__ == "__main__":
    # All interfaces: AgentCore and Docker reach the container from outside it.
    app.run(port=PORT, host="0.0.0.0")  # noqa: S104
