# ai-starter

Crystal Delta's starter for AI assistants that answer customer questions from a client's content,
with citations, safety rules first, and a streaming chat page. Start with
[docs/PLAN.md](docs/PLAN.md) (the work plan, by phase). Design principles and the architecture
decision log are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): follow the principles, and record
any new architecture decision there. Setup and layout are in [README.md](README.md). The
blueprint this implements is the `ai-project` skill (with `ai-project-aws` or `ai-project-azure`).
Those skills and `langfuse` are in [.claude/skills/](.claude/skills/), so they load for anyone
working in the repo; they are copies, so an improvement to one belongs in the shared skill too.

**Organisation style:** professional tone, Australian English and formats. Pricing or contract
questions go to Crystal Delta's Sales team.

**A new project** starts by copying this repo and running `scripts/new_project.py` (README,
"Start a new project"), which renames the package `assistant`, the project `ai-starter`, the
client's name and the link prefix, then deletes itself. Then copy `docs/templates/features.md`
and `feature-register.md` into `docs/`, send the DevOps request (`docs/templates/devops-request.md`)
and set up Langfuse (`docs/langfuse-setup.md`).

**Features document.** `docs/features.md` is client-facing and lists only `Live` rows of
`docs/feature-register.md`; update both in the commit that delivers a feature
(`docs/templates/README.md`).

## How the code fits together

- **One answering path.** `src/assistant/service.py` owns the graph (AD-03): `route` (rules before
  any model call; sets `behaviour`), then `rewrite` (follow-ups only: a model call that writes the
  standalone search, AD-13), then `retrieve`, then `generate` (the model call that writes the
  answer, with the content in the system message). `api.py` and `agentcore.py` (the AgentCore
  Runtime entry point, AD-20) are thin adapters over it. `api.py` reaches it through
  `agent_client.py` (AD-21): in process, or through AgentCore's `/invocations` with
  `AGENT_RUNTIME=agentcore` (by ARN in the cloud, by URL locally). Put answering logic in the
  service, not in a caller.
- **Seams.** The service never imports a provider. Each dependency has a module (`llm.py`,
  `retrieval.py`, `memory.py`, `guardrail.py`, `prompts/`, `tracing.py`) with a factory that reads
  `config.settings` (`LLM_PROVIDER`, `RETRIEVER`, `MEMORY`, `GUARDRAIL`, `PROMPT_SOURCE`).
  `service.build_components()` assembles them; tests replace them with `service.use(...)`. A new
  provider goes in its seam's factory, never in the service. Values not built yet
  (`LLM_PROVIDER=azure_openai`, `MEMORY=agentcore`) are accepted and fail with a clear error.
- **Streaming contract.** `service.astream_answer()` wraps `agent.astream_events(version="v2")` and
  yields `start` / `status` / `sources` / `token` / `replace` / `done` / `error` dicts; `api.py`
  serialises them as SSE, as does `agentcore.py` (through the SDK), and `scripts/chat.py` and
  `frontend/src/api.ts` parse them. Adding an event type means touching all of them. A written
  answer's `sources` come after its tokens: they are the pages the answer cites (`cited`; every
  page when it cites none). A fixed reply's sources (if any) come before its text. `replace`
  comes before the sources when the guardrail blocks an answer already streamed.
- **Behaviour rules** (AD-12): `route` runs `rules.classify` before any search or model call. A
  danger gets the fixed `emergency` reply (citing `rules.EMERGENCY_PAGE` as its one source when a
  project sets it), repeated once on the next turn; an instruction override gets `decline`;
  complaints and account requests are answered with the `handoff` prompt added. After the search,
  a first question or a rewritten follow-up scoring below `RETRIEVAL_MIN_SCORE` is declined.
  Patterns match normalised text (doubled letters collapsed), so write them collapsed ("bleding",
  "kil"); add every new phrasing, and its look-alikes, to `tests/test_rules.py`. **The starter's
  danger patterns and emergency wording are placeholders**: the client's situations and approved
  wording replace them. Never let a model write or change the emergency reply; it must keep `000`
  (`prompts.REQUIRED`). `SAFETY_RULES=false` turns the danger rules off, only for a project with
  no safety-critical situations, agreed with the client and recorded as an AD; the tests pin it
  on, so the mechanism stays tested.
- **Guardrail** (AD-18, `docs/guardrail.md`): `route` checks the question after the rules, so the
  emergency reply never depends on it; `generate` checks the answer against the passages it was
  written from (the grounding check). Fixed replies are not checked. A blocked question or answer
  becomes the guardrail's message (`decline`), in memory too, and a masked question replaces the
  customer's. A failed check allows the text. Each check is a `guardrail` observation under its
  node (`tracing.observe_guardrail`), and the turn gets a `guardrail` score; logs and traces get
  the types found and the grounding scores, never the matched text.
- **Citations** (AD-14): each source carries `url` and `page`, a number per page; the model cites
  "[1]", and the callers make the number a link. `to_sources` drops any URL outside
  `ALLOWED_LINK_PREFIX`, and the page checks `BRAND.linkPrefix` too; never link a URL from the
  model's text.
- **Retrieval** (AD-15): fastembed (ONNX, no torch) over the Qdrant collection (`content`), filled
  by `ingest.py` from `CONTENT_DIR` (Markdown with `title` and `url` front matter; `chunking.py`
  makes one chunk per heading section, AD-16). One page takes at most `RETRIEVAL_MAX_PER_PAGE`
  places; `retrieve` leaves out passages more than `RETRIEVAL_MAX_GAP` below the best. The floor
  and gap were measured on the first project: re-measure with `scripts/verify_retrieval.py`
  (expectations in `evals/retrieval.yaml`, no model involved). A filter by user context (region,
  segment) is a payload field set in chunking and a keyword on `Retriever.search`.
- **Published index** (AD-22): `python -m assistant.publish` builds an embedded index from
  `CONTENT_DIR` and publishes it to `INDEX_STORE` (S3 or a folder): content, index, then
  `LATEST.json` last, holding back an index under 90% of the published one. With `INDEX_STORE`
  set the retriever downloads the published index on its first search and swaps in a newer one
  every `INDEX_CHECK_SECONDS`, so a content change needs no rebuild.
- **Website content** (AD-24, `docs/website-content.md`): `scripts/survey_site.py` (or the
  `site-survey` skill) surveys a site before anything else; then, only with the client's written
  permission, `snapshot.py` crawls the `SITE_SECTIONS` into `data/snapshots/`, `clean.py` writes
  Markdown into `data/content/<id>/` (named by `LATEST`, which `ingest.content_folder` follows),
  and `refresh.py` is the scheduled job: sitemap refresh, clean, then `publish.run` with the raw
  snapshot (the next refresh's baseline, `snapshots/` in the store). A site's own structures go in
  `clean._Renderer`, each with a synthetic test. The politeness rules are in Pitfalls.
- **Prompts** (AD-11) are edited in Langfuse; `prompts/*.md` are the fallback, served when Langfuse
  is unreachable, its keys are unset or a version fails `prompts.problem`, and copied from
  Langfuse at each release by `scripts/pull_prompts.py` (`versions.json`; see
  `docs/prompt-management.md`). So a prompt edit in the repo alone does not reach answers in the
  cloud: change it in Langfuse, or push it with `push_prompts.py --force <name>`. Text code depends
  on (`NONE`, `000`) goes in `prompts.REQUIRED`. Read prompts through
  `components.prompts.get_prompt()` in a node, so the turn records the version (`used`), and give a
  new model call `linked(prompt)` so its trace links to the prompt.
- **Thread IDs** are server-issued and signed (`threads.py`). `api.py` verifies the caller's
  `<key>.<signature>` and passes only the key to the service, which uses it for memory and as the
  Langfuse session ID; `api.py` swaps the signed ID back into responses and `start` / `done`
  events. The signed ID is a credential: never log it or pass it to the service. The AgentCore
  session ID is the key's SHA-256 (`agent_client.session_id`), never the signed ID.
- **Memory** is a LangGraph checkpointer keyed by thread key. Locally it is SQLite (`memory-data`
  volume): survives restarts, one machine only, no expiry. The turn limit is counted from it.
- **Front end** (`frontend/`, AD-08) is a small React page served by Vite with `/api` proxied to
  the API. Every colour, radius and the font are `--brand-*` custom properties at the top of
  `styles.css`; the name and words are in `brand.ts`. Copy no client assets (logo, fonts, images)
  the client has not supplied.
- **Tracing** (AD-10) is on only when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set. The
  SDK is v4 (4.7 or later). A turn runs inside `tracing.attributes`, which puts the session, tags
  and trace name on every observation. Every trace goes through `tracing.mask_pii`, and every
  answer logs `first_status_ms` / `first_token_ms` / `total_ms` (also sent to the trace as scores).
- **Compose** takes its project name from `COMPOSE_PROJECT_NAME` (default `ai-starter`) and each
  host port from `API_PORT`, `QDRANT_PORT`, `LANGFUSE_PORT` and `AGENTCORE_PORT`, so two stacks
  can run side by side.

## Pitfalls

- **Settings are read once, at import.** Use `config.settings.<name>`; in tests change them with
  `monkeypatch.setattr(config.settings, ...)`, not the environment. `tests/conftest.py` pins the
  seams (in-process memory, file prompts, no tracing keys) so a developer's `.env` cannot leak into
  the suite.
- **Never `load_dotenv(..., override=True)`.** `.env` holds host URLs (`localhost:6333`,
  `localhost:11434`); Compose sets container URLs (`qdrant:6333`, `host.docker.internal:11434`).
  With `override=True` the host values win inside Docker and every connection is refused.
- **Container URLs vs host URLs.** Ollama runs on the host, so containers reach it at
  `host.docker.internal`, not `localhost`.
- **The singleton lock in `service.py` is an `RLock`; keep it that way.** The getters nest
  (`get_agent()` holds the lock and calls `get_components()`). With `threading.Lock` the first
  question deadlocks with no exception, no log and no CPU use. A test calls `get_agent()` on a
  worker thread with a timeout. Any new lazy getter must be safe to call with the lock held.
- **The stream needs the checkpointer's async API.** `SqliteSaver` refuses it, so `memory.py` uses
  `ThreadedSqliteSaver`. Any new checkpointer must serve both the sync and the async API
  (`test_seams.py` checks this).
- **The stream matches on node names.** `_StreamedTurn` turns the `route`, `retrieve` and
  `generate` node events into `status`, `sources` and `token` events, and only `generate`'s model
  output is streamed. Rename a node without updating it and statuses or tokens silently vanish;
  put a second model call inside `generate` and its output reaches the user. Give any new model
  call its own node, as `rewrite` has.
- **Embedding model and collection are dimension-locked.** Ingest and query both use
  `EMBEDDING_MODEL`; changing it means re-ingesting. A published index records its model in
  `LATEST.json`; publish holds back, and the agent refuses, one built with another model.
- **Read model output with `.text`, not `.content`.** Bedrock's Converse returns a list of content
  blocks, reasoning among them; `.text` keeps only the answer text.
- **Keep `ReasoningFilter`.** Reasoning models can emit `<think>` blocks inline in content.
- **An embedded index admits one client per process.** The retriever holds it, so nothing else in
  the same process may open `QDRANT_PATH` while it does.
- **Keep `ORT_DISABLE_TELEMETRY=1`** (`assistant/__init__.py`, the Dockerfile, conftest).
  Importing `qdrant_client` loads onnxruntime, whose telemetry thread can abort Python at exit on
  macOS (exit 134) after every test has passed.
- **`requirements.txt` is a lock; edit `requirements.in`** and re-lock with the `uv pip compile`
  command in the README. Keep `--universal`.
- **The crawler's waits and stop on refusal are deliberate.** Never shorten the random wait, send
  requests in parallel, retry around a 429 or a refusing 403, change the user agent, or schedule
  a full crawl; ask the client to allow the crawler instead. No request to a client's site beyond
  the survey before written permission. Raw pages and cleaned content stay in `data/`, never in
  git or `tests/fixtures/`.
- **The test fixtures are synthetic and for unit tests only.** `tests/conftest.py` pins
  `CONTENT_DIR` to `tests/fixtures/content/` and `ALLOWED_LINK_PREFIX` to `example.invalid`. Never
  put client pages in them.

## Testing

```bash
.venv/bin/pytest -q                            # no model, Qdrant or network; fakes in tests/fakes.py
.venv/bin/ruff check . && .venv/bin/ruff format --check .
(cd frontend && npm run lint)                  # Biome; `npm run format` fixes
.venv/bin/python scripts/verify_retrieval.py   # needs the stack running; no model
.venv/bin/python scripts/run_scenarios.py      # behaviour scenarios; needs the stack running
```

Scenario expectations name page titles, checked against `evals/site_titles.txt`. After the content
changes, refresh it with `run_scenarios.py --write-titles` and re-check the expectations. A
behaviour change should come with a scenario.

Before claiming a change works end to end: `docker compose up -d --build`, check
`curl localhost:8000/api/health` (Ollama and Qdrant OK, points > 0), then hold a multi-turn
conversation with `scripts/chat.py` and confirm answers stream with sources.
