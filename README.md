# ai-starter

Crystal Delta's starter repository for AI assistants that answer customer questions from a
client's content: a LangGraph service with safety rules first, retrieval with citations,
follow-up rewriting, a guardrail seam, Langfuse tracing and prompt management, a FastAPI API that
streams, a React chat page, and the images for AWS. A new project copies it, runs one rename
script and has a working local conversation on day one, on synthetic sample content.

It implements the `ai-project` skill (Crystal Delta's blueprint; `ai-project-aws` and
`ai-project-azure` for the cloud side), with the client-specific parts of its origin removed.
Those skills and the `langfuse` skill come with the repo, in [.claude/skills/](.claude/skills/).

Origin: derived from ea-customer-bot (`~/Projects/ea-customer-bot`), the EnergyAustralia customer assistant.

- **Work plan:** [docs/PLAN.md](docs/PLAN.md)
- **Architecture, principles and decisions:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **For Claude Code:** [CLAUDE.md](CLAUDE.md)
- **Document templates** (DevOps request, crawl permission, safety wording, features):
  [docs/templates/](docs/templates/)

## Quick start

Needs Docker, Python 3.13, Node 22 (for the page) and [Ollama](https://ollama.com) on the host.

```bash
ollama pull gpt-oss:20b     # about 13 GB; needs about 16 GB RAM
# or: ollama pull qwen3:8b  # smaller machines; set OLLAMA_MODEL=qwen3:8b in .env

docker compose up -d --build                     # qdrant, ingest (one-shot), api
curl localhost:8000/api/health                   # ollama_ok and qdrant_ok true, points > 0
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt -e .
.venv/bin/python scripts/chat.py                 # "How can I pay my bill?", then "Can I do that at the post office?"
(cd frontend && npm install && npm run dev)      # http://localhost:5173
```

Every setting has a default and no API keys are needed; `cp .env.example .env` to change any.
The stack answers from `content/`, seven synthetic pages of a fictional organisation
(`example.invalid` URLs), until the client's content replaces them.

Another stack already on the default ports? Every host port and the Compose project name are
settings:

```bash
COMPOSE_PROJECT_NAME=ai-starter API_PORT=8100 QDRANT_PORT=6433 docker compose up -d --build
.venv/bin/python scripts/chat.py --api http://localhost:8100
(cd frontend && API_URL=http://localhost:8100 npm run dev)
```

## Start a new project

```bash
cp -R ai-starter acme-helper && cd acme-helper
rm -rf .git .venv data frontend/node_modules frontend/dist src/*.egg-info
python3 scripts/new_project.py --project acme-helper --package acme_helper \
    --client "Acme Services" --link-prefix https://www.acme.example/ --safety-rules yes
git init -b main
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt -e .
.venv/bin/pytest -q
```

`new_project.py` renames the package (`src/assistant/` and every import and command), the project
name (pyproject, the Compose project and image tags, the local Langfuse project, the Langfuse
prompt prefix, trace tags and log names), the client's name in `frontend/src/brand.ts` and the
citation link prefix (`ALLOWED_LINK_PREFIX`, `brand.ts`). `--safety-rules yes|no` is required:
ask the client whether the assistant meets safety-critical situations (someone in danger) before
running it; `no` turns the danger rules off (`SAFETY_RULES=false`) and deletes the emergency
scenarios, keeping the mechanism and its tests so they can be turned on later. Then it prints the next steps and deletes
itself. `--dry-run` lists what it would change. Then, in order (details in
[docs/PLAN.md](docs/PLAN.md)):

1. Intake (the `ai-project` skill's questions) into PLAN.md; confirm or supersede each decision in
   ARCHITECTURE.md marked "Inherited from the starter".
2. With safety rules on: the client's safety-critical situations in `src/<package>/rules.py`
   (with `tests/test_rules.py`) and their approved wording in `prompts/emergency.md`
   (`docs/templates/safety-wording-approval.md`). The starter's patterns and wording are
   placeholders. With them off: record the decision and the client's agreement as an AD.
3. The prompts in `src/<package>/prompts/` for the client's voice and services.
4. The brand: `--brand-*` at the top of `frontend/src/styles.css`, and `frontend/src/brand.ts`.
5. The client's content into `CONTENT_DIR` ([Content](#content)), then the scenarios,
   `evals/retrieval.yaml` and the retrieval thresholds.

## Local stack

| Piece | Where it runs | Notes |
| --- | --- | --- |
| LLM | Ollama, natively on the host | Docker on macOS has no GPU access |
| `api` | Docker, <http://localhost:8000/docs> | FastAPI over `src/assistant/service.py`; reloads on save; conversations in the `memory-data` volume |
| `ingest` | Docker, one-shot | Fills Qdrant from `content/` on a cold volume |
| `qdrant` | Docker, <http://localhost:6333/dashboard> | Vector DB |
| Langfuse | Docker, optional `tracing` profile, <http://localhost:3000> | Off by default |
| `agentcore` | Docker, optional `agentcore` profile, <http://localhost:8080/ping> | The AgentCore Runtime image ([below](#agentcore-image-locally)) |

```bash
docker compose down                                           # stop, keep data
docker compose down -v                                        # stop and wipe the volumes, including conversations
docker compose run --rm ingest python -m assistant.ingest     # re-ingest after a content change
```

With tracing: `docker compose --profile tracing up -d --build`, then set the Langfuse keys in
`.env` (see `.env.example`), recreate the API (`docker compose up -d api`) and open
<http://localhost:3000>. [docs/langfuse-setup.md](docs/langfuse-setup.md) sets up a project's score
configs and dashboard.

## Develop

```bash
.venv/bin/pytest -q                                          # unit tests: no model, Qdrant or network
.venv/bin/ruff check . && .venv/bin/ruff format --check .    # Python lint and format
(cd frontend && npm run lint && npm run build)               # Biome, TypeScript
.venv/bin/python scripts/verify_retrieval.py                 # retrieval check on the ingested content; no model
.venv/bin/python scripts/run_scenarios.py                    # behaviour scenarios through the running API
```

The behaviour scenarios in `evals/scenarios/` are scripted conversations, one YAML file per
category (emergency, follow-up, handoff, not-in-content, out-of-scope). Each turn gives the
expected behaviour, the pages it should cite, phrases it must or must not contain, and an `ideal`
answer in words. Locally only these deterministic checks run, plus checks that no phone number
or price appears that is not in the cited content and that the answer has no tables or headings;
answer quality is judged against `ideal` on the production model in Phase 6. `--only <id>` runs
one scenario, `-v` shows every check and answer, `--api` points at another port. Do not expect
every scenario to pass on a small local model: compare runs.

Each answer logs its timings and the prompt versions it used (`answered thread=…
first_status_ms=… first_token_ms=… total_ms=… prompts=system:file guardrail=-`), and sends the
timings to the Langfuse trace as scores when tracing is on.

### Content

The pipeline starts from Markdown: one file per page in `CONTENT_DIR` (`content/` by default),
with front matter:

```markdown
---
title: Ways to pay your bill
url: https://www.client.example/help/ways-to-pay
---

# Ways to pay your bill
...
```

`ingest.py` chunks each page by heading (`chunking.py`) and embeds the chunks. Getting the
Markdown is the project's Phase 3, with the client's written permission: a CMS export, documents
converted to Markdown, or a crawl of their website. Keep client content out of git unless the
client agrees; point `CONTENT_DIR` at a git-ignored folder under `data/`.

**A website** goes through [docs/website-content.md](docs/website-content.md): survey it first,
then get permission, crawl, clean, and refresh it on a schedule in the cloud. New to it? Do
[the tutorial](docs/website-content-tutorial.md) first: the whole pipeline on Crystal Delta's own
site, step by step, in about 30 minutes. In short:

```bash
.venv/bin/python scripts/survey_site.py https://www.client.example   # can it be crawled, and how? ~15 requests
.venv/bin/python -m assistant.snapshot --max-pages 10                # after written permission: a trial
.venv/bin/python -m assistant.snapshot                               # the SITE_SECTIONS, politely
.venv/bin/python -m assistant.clean                                  # HTML to Markdown in data/content/
CONTENT_DIR=data/content .venv/bin/python -m assistant.ingest
```

After a content change: re-ingest, refresh `evals/site_titles.txt`
(`scripts/run_scenarios.py --write-titles`), update `evals/retrieval.yaml` and the scenarios, and
re-measure `RETRIEVAL_MIN_SCORE` and `RETRIEVAL_MAX_GAP` with `scripts/verify_retrieval.py`.

### Prompts

Prompts are edited in Langfuse prompt management (AD-11). With `PROMPT_SOURCE=langfuse` the
service serves the `production` version of each; the files in `src/assistant/prompts/` are the
fallback whenever Langfuse is unreachable, its keys are unset, or a version fails the checks.
[docs/prompt-management.md](docs/prompt-management.md) covers editing, testing, rolling back and
releasing:

```bash
.venv/bin/python scripts/push_prompts.py --dry-run   # seed a Langfuse project: creates missing prompts
.venv/bin/python scripts/pull_prompts.py             # at each release: copy Langfuse into the files
.venv/bin/python scripts/pull_prompts.py --check     # exit 1 if the files differ from Langfuse
```

### Dependencies

`requirements.in` lists the direct dependencies; `requirements.txt` is the lock compiled from it
and is what the image and the venv install. Change pins only by re-locking:

```bash
.venv/bin/pip install uv    # once
.venv/bin/uv pip compile requirements.in --universal --python-version 3.13 -o requirements.txt
.venv/bin/pip install -r requirements.txt
```

`--universal` resolves for every platform, so a lock made on macOS also installs in the Linux
image.

## Settings

Every setting is in [.env.example](.env.example) with its default. The main ones:

| Variable | Default | Notes |
| --- | --- | --- |
| `LLM_PROVIDER` | `ollama` | `bedrock`; `azure_openai` is accepted and fails clearly until built (`ai-project-azure` skill) |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | `http://localhost:11434`, `gpt-oss:20b` | Compose sets the container URL |
| `BEDROCK_MODEL_ID`, `BEDROCK_TEMPERATURE`, `AWS_REGION` | unset, unset, `ap-southeast-2` | Any Converse model; never hardcode one |
| `RETRIEVER` | `qdrant` | |
| `QDRANT_URL`, `QDRANT_PATH`, `QDRANT_COLLECTION` | `http://localhost:6333`, unset, `content` | `QDRANT_PATH`: an embedded index instead of the server |
| `INDEX_STORE`, `INDEX_CHECK_SECONDS` | unset, `43200` | The published index (`s3://bucket/prefix` or a folder), used instead of Qdrant when set |
| `CONTENT_DIR` | `content` | Markdown pages for ingest and publish; `data/content` for a cleaned website snapshot |
| `SITE_URL`, `SITE_SECTIONS`, `SITE_CONTENT_SELECTOR`, `SITE_OWN_403_HEADER` | unset, unset (the whole site), `main, [role=main], article`, unset | Website content ([docs/website-content.md](docs/website-content.md)); `scripts/survey_site.py` suggests them |
| `ALLOWED_LINK_PREFIX` | `https://example.invalid/` | Only source URLs under it become links: the client's site |
| `RETRIEVAL_TOP_K`, `RETRIEVAL_MAX_PER_PAGE` | 5, 1 | Places, and places per page |
| `RETRIEVAL_MIN_SCORE`, `RETRIEVAL_MAX_GAP` | 0.62, 0.1 | Score floor for declining off-topic questions, and the gap cut; re-measure on the client's content |
| `MEMORY`, `MEMORY_PATH` | `sqlite`, `data/memory.sqlite` | `in_process`; `agentcore` arrives in Phase 6 |
| `SAFETY_RULES` | `true` | `false` turns off the danger rules and the fixed `emergency` reply, for a project with no safety-critical situations (agreed with the client) |
| `GUARDRAIL`, `GUARDRAIL_ID`, `GUARDRAIL_VERSION` | `noop` | `bedrock` applies a Bedrock Guardrail ([docs/guardrail.md](docs/guardrail.md)) |
| `PROMPT_SOURCE`, `PROMPT_LABEL` | `file`, `production` | `langfuse` serves Langfuse, with the files as fallback |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | unset | Tracing and Langfuse prompts; off without the keys |
| `THREAD_ID_SECRET` | unset | 32+ characters for anything shared; unset means a random secret per process |
| `MAX_QUESTION_CHARS`, `MAX_TURNS_PER_THREAD`, `RATE_LIMIT_PER_MINUTE` | 2000, 30, 20 | Abuse limits |
| `CLIENT_ADDRESS_HEADER` | unset | Behind CloudFront: `CloudFront-Viewer-Address` |
| `AGENT_RUNTIME`, `AGENTCORE_URL`, `AGENTCORE_RUNTIME_ARN` | `in_process` | `agentcore`: the API runs turns on the agent runtime |

Compose only: `COMPOSE_PROJECT_NAME` (`ai-starter`), `API_PORT` (8000), `QDRANT_PORT` (6333),
`LANGFUSE_PORT` (3000), `AGENTCORE_PORT` (8080).

## API

A conversation is identified by a server-issued, signed `thread_id`. Omit it on the first question
and send back the one from the response (or from the stream's `start` event) on each later turn.

```bash
curl -s localhost:8000/api/ask -H 'content-type: application/json' -d '{"question": "How can I pay my bill?"}'
```

`POST /api/ask/stream` runs the same turn as server-sent events: `start`, `status` (`searching`,
`generating`), `token`, `replace` (the guardrail withdrew the streamed answer), `sources`, `done`,
`error`. `GET /api/health` reports each dependency.

| Status | Meaning |
| --- | --- |
| 400 | `thread_id` is unsigned or forged. Omit it to start a new conversation |
| 409 | The conversation has reached `MAX_TURNS_PER_THREAD` |
| 422 | Invalid request: an unknown field, or an empty or overlong question |
| 429 | More than `RATE_LIMIT_PER_MINUTE` requests from this client; see `Retry-After` |
| 502 | The run failed; the details are in the API log |

## Cloud

The cloud side follows the `ai-project-aws` skill (AWS) or `ai-project-azure` skill (Azure),
which hold the staged DevOps requests, IAM, Terraform hand-over and runbooks. What this repo
provides for AWS:

| Component | Dockerfile target | Runs on | Notes |
| --- | --- | --- | --- |
| Agent | `agentcore` (the default) | AgentCore Runtime, arm64 | `GET /ping`, `POST /invocations` on 8080; `LLM_PROVIDER=bedrock` and `INDEX_STORE` set on the runtime |
| API | `lambda` (with `--provenance=false`) | Lambda behind CloudFront | The API with the Lambda Web Adapter, calling the agent by ARN |
| Content | none (`python -m assistant.publish`) | A developer machine | Publishes the index to S3 |
| Website refresh | `refresh` | A scheduled container task (ECS Fargate) | Refreshes the site from its sitemap and publishes ([docs/website-content.md](docs/website-content.md)) |

```bash
docker buildx build --platform linux/arm64 -t ai-starter-agentcore:$(git rev-parse --short HEAD) .
docker buildx build --platform linux/arm64 --provenance=false --target lambda \
  -t ai-starter-lambda:$(git rev-parse --short HEAD) .
INDEX_STORE=s3://<bucket>/content AWS_PROFILE=<profile> .venv/bin/python -m assistant.publish
```

Tag images with the commit SHA, never deploy `latest`, and record the previous tag as the
rollback. Before building, bring the prompt files in step with Langfuse
(`scripts/pull_prompts.py --check`). The agent's role needs `bedrock:InvokeModel` and
`bedrock:InvokeModelWithResponseStream` on the model and `s3:GetObject` on `LATEST.json` and
`indexes/*`; with the guardrail, `bedrock:ApplyGuardrail`. The API's role needs
`bedrock-agentcore:InvokeAgentRuntime`. The hosted demo's password is a CloudFront function
([deploy/cloudfront-password.js](deploy/cloudfront-password.js)), and the guardrail's definition
is [deploy/guardrail.json](deploy/guardrail.json). [docs/CI-CD.md](docs/CI-CD.md) lists the
manual steps a pipeline must take over.

### AgentCore image locally

The `agentcore` Compose profile runs the agent image with Ollama on the host, searching an index
published to a local store in `data/store` (git-ignored):

```bash
INDEX_STORE=data/store .venv/bin/python -m assistant.publish
docker compose --profile agentcore up -d --build agentcore
.venv/bin/python scripts/chat.py --agentcore http://localhost:8080
```

To send the local API's turns through it, as in the cloud, set `AGENT_RUNTIME=agentcore` in `.env`
and recreate the `api` service. To use Bedrock from it, set `AGENTCORE_LLM_PROVIDER=bedrock`,
`BEDROCK_MODEL_ID` and `AWS_PROFILE` (`~/.aws` is mounted read-only).

## Layout

```
src/assistant/
  service.py     answering core: graph (route, rewrite, retrieve, generate), streaming events, timings
  rules.py       behaviour rules before any model call: the client's domain rules go here
  api.py         FastAPI: /api/health, /api/ask, /api/ask/stream; limits
  agentcore.py   AgentCore Runtime entry point: /ping, /invocations (port 8080)
  agent_client.py  where the API runs a turn: in process or on AgentCore
  threads.py     server-issued, signed thread IDs
  config.py      typed settings from the environment; picks each seam
  llm.py         seam: chat model (Ollama, Bedrock; Azure OpenAI not built)
  retrieval.py   seam: content passages (Qdrant + ONNX embeddings, or the published index)
  memory.py      seam: conversation checkpointer (SQLite, in process)
  guardrail.py   seam: content checks (no-op, or a Bedrock Guardrail)
  prompts/       seam: prompts from Langfuse, these files as fallback (versions.json)
  tracing.py     seam: Langfuse traces, PII masking, timing scores
  ingest.py      Markdown content -> chunks -> Qdrant (server or embedded index)
  chunking.py    one chunk per heading section
  publish.py     content -> embedded index -> index store, LATEST.json last
  index_store.py the published content and indexes (S3, or a local folder)
  snapshot.py    website: polite crawl of the sections, or a refresh from the sitemap
  clean.py       website: snapshot HTML -> Markdown in data/content/
  refresh.py     website: the scheduled refresh: sitemap, changed pages, clean, publish
content/               synthetic sample pages the stack serves until the client's replace them
scripts/
  new_project.py       rename the starter for a new project (deletes itself)
  survey_site.py       survey a website before crawling it; a report and suggested settings
  chat.py              terminal chat client
  run_scenarios.py     run the behaviour scenarios
  verify_retrieval.py  retrieval check, expectations in evals/retrieval.yaml
  push_prompts.py      seed Langfuse with the prompts it lacks
  pull_prompts.py      copy the Langfuse prompts into the repo files (each release)
evals/
  scenarios/           scripted conversations, one file per category
  scenarios.py         loading, checks and running
  site_titles.txt      the page titles scenarios may cite
  retrieval.yaml       retrieval expectations
frontend/              React chat page (Vite); src/brand.ts and the top of src/styles.css hold the brand
deploy/                cloud resources made outside Terraform: guardrail, CloudFront password function
docs/                  PLAN, ARCHITECTURE, CI-CD, website content, prompt management, guardrail, Langfuse setup, templates
tests/
  fakes.py             in-memory fakes for every seam
  fixtures/content/    synthetic pages for the unit tests
```
