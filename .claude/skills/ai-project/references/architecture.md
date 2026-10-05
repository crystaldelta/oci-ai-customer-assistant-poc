# Architecture: service, seams, graph, streaming, API

Reference implementation: `~/Projects/ea-customer-bot/src/ea_customer_bot/` (`service.py`,
`config.py`, `llm.py`, `guardrail.py`, `api.py`, `agent_client.py`, `threads.py`).

## Contents

1. Settings and seams
2. Components and the singleton
3. The graph
4. The streaming contract
5. API, thread IDs and limits
6. Where a turn runs (in process or the cloud runtime)
7. Local stack (Compose)
8. Dockerfile targets

## 1. Settings and seams

`config.py` holds one `Settings(BaseSettings)` and a module-level `settings = Settings()`. Each
seam is a `Literal` setting whose default is the local implementation:

```python
llm_provider: Literal["ollama", "bedrock", "azure_openai"] = "ollama"
retriever: Literal["qdrant", "knowledge_base", "ai_search"] = "qdrant"
memory: Literal["sqlite", "in_process", "<cloud>"] = "sqlite"
guardrail: Literal["noop", "<cloud>"] = "noop"
prompt_source: Literal["file", "langfuse"] = "file"
```

- `load_dotenv(ROOT / ".env", override=False)` at the top of `config.py`. **Never
  `override=True`**: `.env` holds host URLs and Compose sets container URLs.
- Cloud values are accepted before they are built, and their factory raises a clear
  `NotImplementedError("… arrives in Phase 6")`, so the choice is visible.
- Every tuning number is a setting with a comment saying what it was measured against and when to
  re-measure (score floors, gaps, margins).
- The model is always a setting (`BEDROCK_MODEL_ID`, `AZURE_OPENAI_DEPLOYMENT`). Never hardcode a
  model ID.

A seam module looks like this (see `guardrail.py`):

```python
class Guardrail(Protocol):
    def check(self, text: str, *, source: Literal["input", "output"], grounding: str | None = None) -> GuardrailResult: ...

class NoopGuardrail: ...            # local: logs sizes, allows
class CloudGuardrail: ...           # cloud: imports its SDK lazily, inside the class

def get_guardrail(settings: Settings) -> Guardrail:
    if settings.guardrail == "noop": return NoopGuardrail()
    if settings.guardrail == "<cloud>": return CloudGuardrail(...)
    raise NotImplementedError(...)
```

Rules: construction makes **no network call**; provider SDKs are imported inside the branch that
needs them; results are small frozen dataclasses the service understands, never provider objects.

## 2. Components and the singleton

```python
@dataclass
class Components:            # one implementation per seam
    llm: BaseChatModel; model: str; retriever: Retriever; checkpointer: BaseCheckpointSaver
    guardrail: Guardrail; prompts: PromptSource; tools: ToolSet | None = None

def build_components(settings) -> Components: ...
_lock = threading.RLock()    # RLock: get_agent() holds it and calls get_components()
def get_components(): ...    # lazy, cached
def use(components): ...     # tests inject fakes; None rebuilds from settings
def get_agent(): ...         # compiled graph, cached
```

## 3. The graph

Start with an explicit graph, not a ReAct loop: it removes a model call per turn and gives the
safety rules a place to run first. The shape that worked:

```
route ──(fixed reply)──────────────────────────────▶ END
  │
  ├─(follow-up)─▶ rewrite ─▶ retrieve ─▶ generate ─▶ END
  └────────────────────────▶ retrieve ─(below score floor: decline)─▶ END
```

- **route:** rules (`rules.classify`) before any model call; sets `behaviour` (`answer`,
  `clarify`, `emergency`, `handoff`, `decline`, …); then the input guardrail.
- **rewrite:** follow-ups only. One short model call that turns the last few exchanges into a
  standalone search; `NONE` for "thanks". Never streamed, never stored.
- **retrieve:** search, filters, score floor, gap cut, one passage per page.
- **generate:** the one model call whose tokens stream; content in the system message; then the
  output guardrail with the passages as grounding source.
- **State** (`TurnState(MessagesState)`): `behaviour`, `sources`, `search`, user context (state,
  segment), `prompts` used with versions, guardrail report.
- Each node reads prompts through `components.prompts.get_prompt()` and records the version.
- Name nodes as constants (`ROUTE = "route"`): the stream matches on them.
- Tool-using and multi-agent graphs: `references/tools-and-agents.md`.

## 4. The streaming contract

`service.astream_answer()` wraps `agent.astream_events(version="v2")` and yields dicts:

| Event | When |
| --- | --- |
| `start` | First, with the thread ID |
| `status` | Progress ("searching", "writing"; tool names) |
| `location` / domain events | UI prompts such as choice buttons |
| `token` | Visible answer text, from the answering node only |
| `replace` | The guardrail blocked an answer already streamed |
| `sources` | After the tokens: only the sources the answer cites |
| `done` | Last, with behaviour, timings and thread ID |
| `error` | A clean message; details only in the log |

Adding an event touches every consumer: `api.py` (SSE), the cloud entry point, `scripts/chat.py`
and `frontend/src/api.ts`. A contract test guards the order. A `ReasoningFilter` strips inline
`<think>` blocks from tokens; read model output with `.text`, not `.content`.

## 5. API, thread IDs and limits

- `POST /api/ask` (JSON), `POST /api/ask/stream` (SSE), `GET /api/health` (each seam's status,
  points indexed, prompt fallbacks).
- **Signed thread IDs** (`threads.py`): the server issues `<key>.<hmac>`; a client cannot choose or
  guess another's conversation. The API verifies and passes only the key to the service, which
  uses it for memory and as the Langfuse session ID. The signed ID is a credential: never log it.
  `THREAD_ID_SECRET` must be set on anything shared.
- **Limits** (anonymous endpoints cost tokens): question length (422), turns per thread counted
  from memory (409), requests per minute per client address (429). Do not trust
  `X-Forwarded-For`; read the edge's own header (`CLIENT_ADDRESS_HEADER`).
- Pydantic request models with `extra="forbid"`: unknown fields are a 422, not silently ignored.
- Status codes: 400 bad thread ID, 409 limit, 422 invalid, 429 rate, 502 run failed.
- Signed-in users (tools acting on their data): replace anonymous thread IDs with the identity
  provider's token, verified in the API; the user ID goes into the graph's config, never the
  prompt.

## 6. Where a turn runs

`agent_client.py`: `AGENT_RUNTIME=in_process` calls the service directly (local); `=<cloud>`
calls the cloud agent runtime (by ARN or resource ID in the cloud, by URL against the local
container). The browser never calls the agent runtime directly. The API holds what a public
endpoint needs (signed IDs, limits) and sends the runtime a hash of the thread key as its session
ID.

## 7. Local stack

`docker-compose.yml`: `api` (bind-mounts `./src`, `uvicorn --reload`, plus `--reload-include
"*.md"` for prompt files), `qdrant` (health check by TCP), `ingest` (one-shot, `--if-empty`,
`api` depends on it completing), profiles `tracing` (Langfuse v3 server with Postgres,
ClickHouse, Redis, MinIO; `LANGFUSE_INIT_*` seeds a project with fixed local keys) and `<runtime>`
(the cloud agent image on its port, mounting `~/.aws` or Azure credentials read-only).

- Containers reach host services at `host.docker.internal` (add `extra_hosts:
  host.docker.internal:host-gateway` for Linux).
- `environment:` in Compose overrides `env_file`, so container URLs go there.
- The slim Python image has no curl: health checks use `python -c "urllib.request…"`.

## 8. Dockerfile targets

One `base` (deps from the lock, fail the build if torch is installed, `COPY src`), then:

- `api`: uvicorn on 8000 (the local stack).
- `models`: bakes the embedding model in (`HF_HUB_OFFLINE=1` after) and adds a non-root user.
- `<runtime>`: the cloud agent entry point, from `models`.
- `refresh` or other jobs: from `models`, `CMD` runs the job.
- `api-on-cloud`: the API packaged for the cloud's serverless or container host.

Build for `linux/arm64` where the target runs on arm (AgentCore requires it; cheaper elsewhere).
