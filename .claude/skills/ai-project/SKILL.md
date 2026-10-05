---
name: ai-project
description: Crystal Delta's blueprint for structuring and building a new AI application in Python with LangGraph, Langfuse and a React front end - conversational assistants, RAG over a client's content, tool-using agents and multi-agent systems. Local-first, so development starts before any cloud environment or DevOps help exists. Use this whenever starting, scaffolding, planning, estimating or reviewing a new AI, LLM, agent, chatbot or RAG project, or adding a major capability (tools, agents, guardrails, evaluation, prompt management) to one, even if the user does not mention a blueprint. Use with ai-project-aws or ai-project-azure for the cloud side.
---

# AI project blueprint

Distilled from **ea-customer-bot** (the EnergyAustralia customer assistant, Sep–Oct 2026). It is
the reference implementation: when a pattern here is unclear, read its code at
`~/Projects/ea-customer-bot` (start with `CLAUDE.md` and `docs/ARCHITECTURE.md`). Tools and
multi-agent patterns were **not** built there; `references/tools-and-agents.md` is a design to
validate on the first project that needs it.

Organisation style: professional tone, Australian English and formats. Pricing or contract
questions go to Crystal Delta's Sales team.

## Starter repo

`~/Projects/ai-starter` implements Phases 1–2 of this blueprint: a working local stack with every
seam, the streaming contract, rules, guardrail, prompts, tracing, tests and scenarios, on a
synthetic sample corpus. Start every new project from it: copy it, run
`scripts/new_project.py`, then follow its README. It also holds:

- `docs/templates/`: features document and feature register, DevOps request, crawl permission,
  safety wording approval, data-residency exception, demo covering message, retrospective;
- `scripts/setup_langfuse.py` with `deploy/langfuse-setup.json`: score configs and the health
  dashboard for a Langfuse project (`docs/langfuse-setup.md`).

What the first build took and where time went: `~/Projects/ea-customer-bot/docs/retrospective.md`.

## How to use this skill

1. **Intake.** Ask the questions below before writing code. Unknowns go in `docs/PLAN.md` under
   "Decisions needed" (`D1`, `D2`, …), never silently defaulted.
2. **Cloud.** Read the `ai-project-aws` or `ai-project-azure` skill too. Everything in this
   skill is cloud-neutral.
3. **Scaffold** Phase 0 and 1 (the layout below) so the local stack answers a question on day one.
4. **Work the phases** in order, reading the references as each becomes relevant:

| Read | When |
| --- | --- |
| `references/architecture.md` | Scaffolding; the service, seams, graph, streaming contract, API |
| `references/safety.md` | Before the first public demo: rules first, guardrails, citations, PII, limits |
| `references/tools-and-agents.md` | The assistant calls systems or more than one agent is needed |
| `references/content-pipeline.md` | Answers come from a website or documents (RAG) |
| `references/observability-and-prompts.md` | Langfuse tracing, prompt management, timings |
| `references/testing-and-evals.md` | Tests, fakes, behaviour scenarios, evaluation |
| `references/pitfalls.md` | Before debugging anything odd; when reviewing |
| `assets/` | Templates: `CLAUDE.template.md`, `ARCHITECTURE.template.md`, `PLAN.template.md` |

## Intake: ask the user

- **Client, project and package name** (`<client>-<purpose>`, package `<client>_<purpose>`).
- **What it does:** answers from content, acts through tools (which systems, read or write), or
  several agents. **Who uses it:** anonymous public visitors, signed-in customers or staff. This
  decides authentication and which tools are allowed at all.
- **Safety rules: always ask, never assume either way.** Will users describe situations where
  someone may be in danger, or that need an urgent fixed route? Energy: gas leak, power line down.
  Banking: fraud, scams. Health or any public service: self-harm, someone in danger. An internal
  staff tool over policy documents may have none.
  - **Yes:** list each situation, the exact fixed reply (emergency number, the one link it cites),
    look-alikes that must not trigger it, and who approves the wording (always the client;
    `docs/templates/safety-wording-approval.md`). Starter: `new_project.py --safety-rules yes`;
    the starter's patterns and wording are placeholders to replace.
  - **No:** record the decision and the client's agreement as an AD. Starter:
    `--safety-rules no` (sets `SAFETY_RULES=false`; the mechanism stays, and is tested, so it can
    be turned on later).
  - **Unsure:** default to yes and raise it as a `D` question for the client.
- **Content and permission:** which sources, written permission to crawl or ingest them, and how
  fresh they must be.
- **Branding:** colours, font (licensed fonts get a free stand-in such as Nunito), logo and images
  (use only what the client supplies; never copy site assets), tone and style guide, locale
  (default Australian English).
- **Data residency:** the region, and which services may leave the country (model inference,
  traces). Any exception is a dated, time-limited decision.
- **Cloud and ownership:** AWS or Azure; what DevOps owns; what a developer may create alone in
  a sandbox or test account.
- **Out-of-scope routing:** where pricing, contracts, complaints and account requests go.

## Principles

Copy these into `docs/ARCHITECTURE.md` and adapt. Breaking one needs a recorded decision.

1. **Local first; never wait for DevOps.** Every cloud dependency has a local implementation
   chosen by a setting. The whole stack runs on a laptop with `docker compose` and a local model,
   with no keys. Cloud resources are requested in stages, early and in parallel, while
   development continues locally. A developer may create a resource themselves in a test account
   from a definition file kept in `deploy/`, so DevOps can import it into Terraform later.
2. **One answering path.** All behaviour lives in `service.py`. The API, terminal client, cloud
   entry point and scripts are thin adapters over it.
3. **Every provider sits behind a seam:** one module per dependency, a factory reading settings,
   a fake for tests. Swapping a provider is a settings change, never a change in the service.
4. **Local proves function; the production model proves quality.** Never tune prompts or judge
   answers on the local model.
5. **Grounded answers only.** Answer from retrieved content or tool results, cite them, and
   never invent prices, numbers, policies or links. Say so when the content does not answer.
6. **Safety-critical replies are deterministic.** Where the project has safety-critical
   situations (an intake question; turning them off is a recorded decision), they are detected by
   rules before any model call, biased towards false positives, with fixed wording approved by the
   client. Never let a model write them.
7. **Private by default.** Mask PII before traces leave the process; short-term memory only;
   retention set deliberately.
8. **Optional services never block answering.** Tracing and prompt management can be down; the
   service falls back to repo files and answers untraced.
9. **Lean runtime.** Managed services over self-run ones; small dependencies (ONNX embeddings,
   not torch).
10. **Speed is measured.** Every answer records time to first status, first token and total.
    Each extra model call in a turn needs a reason.
11. **Least privilege for actions.** A tool acts with the user's identity from the server, never
    an identifier the model supplied; anything that changes data needs explicit confirmation.

## Default stack

| Concern | Default | Why |
| --- | --- | --- |
| Language | Python 3.13 (agent, API, pipelines); TypeScript for the browser | LangGraph and the cloud agent SDKs are Python-first |
| Agent framework | LangGraph, an explicit graph (not a ReAct loop by default) | Deterministic routing and safety; fewer model calls |
| API | FastAPI, SSE streaming | Thin adapter over the service |
| Settings | `pydantic-settings`, one `config.settings` | Typed; seams chosen here |
| Local model | Ollama on the host (`gpt-oss:20b`; `qwen3:8b` on small machines) | No keys; Docker on macOS has no GPU |
| Embeddings | fastembed (ONNX), `BAAI/bge-base-en-v1.5` | Small image; dimension-locked to the index |
| Vector store | Qdrant: a server locally, an embedded index file in the cloud | No always-on vector service needed |
| Memory | LangGraph checkpointer: SQLite locally, the cloud's managed memory later | |
| Tracing and prompts | Langfuse SDK v4 (Cloud, or the local `tracing` profile) | Traces, prompt versions, datasets, scores |
| Front end | React + Vite + TypeScript, Biome | Small local demo page; `/api` proxied, no CORS |
| Dependencies | `requirements.in` → `uv pip compile --universal --python-version 3.13` | A lock made on macOS works in the Linux image |
| Quality | pytest (no network), ruff, Biome | |
| Images | One Dockerfile, one target per runtime, arm64 | |
| Infrastructure | Terraform, owned by DevOps | Developer-made resources are imported later |

## Layout

```
CLAUDE.md                 how the code fits together, pitfalls, test commands (assets/CLAUDE.template.md)
README.md                 setup, local stack, settings tables, cloud deployment
.env.example              every setting with its default; tracked. .env is git-ignored
Dockerfile                targets: api (local), <cloud agent runtime>, refresh/jobs, api-on-cloud
docker-compose.yml        api, qdrant, ingest (one-shot); profiles: tracing (Langfuse), <runtime>
requirements.in / .txt    direct dependencies / universal lock
pyproject.toml            ruff, pytest
src/<pkg>/
  service.py              the graph, streaming events, timings: all answering logic
  api.py                  FastAPI: /api/health, /api/ask, /api/ask/stream; limits
  <runtime>.py            cloud agent entry point, a thin adapter
  agent_client.py         where the API runs a turn: in process or on the cloud runtime
  config.py               settings; picks each seam
  threads.py              server-issued, signed thread IDs
  rules.py                behaviour rules run before any model call
  llm.py retrieval.py memory.py guardrail.py tracing.py tools/ prompts/   one seam each
  ingest.py chunking.py snapshot.py clean.py refresh.py index_store.py    content pipeline
scripts/                  chat.py (terminal client), run_scenarios.py, push/pull_prompts.py, verify_retrieval.py
evals/scenarios/          scripted conversations, one YAML file per behaviour category
frontend/                 React page; src/api.ts parses the stream
deploy/                   cloud resource definitions made outside Terraform (guardrail JSON, edge functions)
docs/                     PLAN.md, ARCHITECTURE.md, CI-CD.md, features.md, runbooks, issues/
tests/                    conftest.py (pins seams), fakes.py, fixtures/ (synthetic only)
```

## Phases

Write them into `docs/PLAN.md` (template in `assets/`) with a **Done when** for each, and tick
items with the date and the evidence.

0. **Setup.** Repo, intake answers, `CLAUDE.md`, `ARCHITECTURE.md` with principles, and the staged
   DevOps request (stage 0: account, model access, cost alert, Langfuse project) sent on day one.
1. **Foundations.** Service with a minimal graph, API with streaming, signed thread IDs, abuse
   limits, terminal client, Compose stack, tests with fakes. Done when a local multi-turn
   conversation streams.
2. **Seams.** One module per dependency with local and cloud values in settings (cloud values
   fail clearly until built), fakes, PII masking, prompt fallback, timings.
3. **Content or tools.** The content pipeline (snapshot, clean, chunk, ingest), or the first tools
   behind their seam with fakes.
4. **Behaviour.** Rules first, follow-up rewriting, clarifying questions, citations, handoffs,
   behaviour scenarios. Most of the product's quality is decided here.
5. **Cloud readiness, still local.** The cloud runtime image and entry point run locally under a
   Compose profile; the API can call it as it will in the cloud.
6. **Cloud test environment and evaluation.** Swap seams one at a time to cloud providers;
   guardrail; prompts in Langfuse; evaluation baseline on the production model; latency targets.
- **Later.** Production, CI/CD (keep `docs/CI-CD.md` as the checklist of manual steps a pipeline
  must take over), alarms, long-term memory.

## Working rules

- **Decisions:** `AD-nn` rows in `docs/ARCHITECTURE.md` (date, area, decision, why, status), never
  renumbered or deleted; superseded by a new row. Open questions are `Dn` in PLAN.md.
- **Keep `CLAUDE.md` current:** how the code fits together, pitfalls hit, test commands. It is
  what makes the next session productive.
- **Before claiming a change works end to end:** `docker compose up -d --build`, check
  `/api/health`, hold a multi-turn conversation with `scripts/chat.py`, confirm streaming and
  sources. A behaviour change comes with a scenario.
- **Images:** tag with the commit SHA; never deploy `latest`; record the previous tag as the
  rollback.
- **Client-facing capability doc** (`docs/features.md`): dot points, only what is built and
  running, no commercial model, cost or open questions. Kept honest by `docs/feature-register.md`
  (Planned → Built → Live, with evidence); update both in the commit that delivers a feature.
- **Retrospective at each milestone** (`docs/templates/retrospective.md`), then feed new pitfalls
  and lessons back into this skill and the starter repo.
- **Issues:** `docs/issues/issue-nnn-<slug>.md` for defects found in review, linked from the plan.
