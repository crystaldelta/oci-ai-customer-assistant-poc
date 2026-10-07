# ai-starter: work plan

**Owner:** <name> · **Updated:** 5 Oct 2026

Design and decisions: [ARCHITECTURE.md](ARCHITECTURE.md). Tick an item with the date and the
evidence it works (what was run, what it showed).

This plan came with the starter. Phases 1 and 2 are **provided by the starter**: confirm them on
the new project's machine (the checks are listed), then work from Phase 0 and Phase 3.

## Phases

### Phase 0 — Setup

- [ ] Intake answers recorded (the `ai-project` skill's intake questions); unknowns listed under
      [Decisions needed](#decisions-needed)
- [ ] `scripts/new_project.py` run; repo created; `CLAUDE.md` and README reviewed for the project
- [ ] `ARCHITECTURE.md`: each decision marked "Inherited from the starter" confirmed or superseded
- [ ] DevOps stage 0 requested (needs and reasons only; naming is DevOps' call;
      `docs/templates/devops-request.md`)
- [ ] Langfuse project per environment ([langfuse-setup.md](langfuse-setup.md))

**Done when:** the repo exists, the decisions are confirmed and the stage 0 request is with DevOps.

### Phase 1 — Foundations (provided by the starter)

- [ ] Service with the graph; API with streaming; signed thread IDs; limits
- [ ] Compose stack; `scripts/chat.py`; front-end page with the brand tokens in one place
- [ ] Tests with fakes; ruff; Biome

**Check on the new project:** `.venv/bin/pytest -q` passes; `docker compose up -d --build`, then
`curl localhost:8000/api/health` shows Ollama and Qdrant OK with points > 0, and a two-turn
conversation with `scripts/chat.py` streams with sources.

**Done when:** a local multi-turn conversation streams, and the tests pass with no network.

### Phase 2 — Seams for every cloud dependency (provided by the starter)

| Seam | Local | Cloud | Setting |
| --- | --- | --- | --- |
| `llm` | Ollama | Bedrock (Converse); Azure OpenAI not built | `LLM_PROVIDER` |
| `retriever` | Qdrant + ONNX | The published embedded index (`INDEX_STORE`) | `RETRIEVER` |
| `memory` | SQLite | AgentCore Memory (Phase 6, not built) | `MEMORY` |
| `guardrail` | No-op | Bedrock Guardrail | `GUARDRAIL` |
| `prompts` | Files / local Langfuse | Langfuse, files as fallback | `PROMPT_SOURCE` |
| `tracing` | Off / local Langfuse | Langfuse | `LANGFUSE_*` |

**Done when:** swapping a provider is a settings change only; PII masking and timings are in.
(Provided; extend the table when a seam is added, such as `tools`.)

### Phase 3 — Content pipeline / tools

- [ ] For a website: the site surveyed (`scripts/survey_site.py`, or the `site-survey` skill);
      verdict, settings and findings in `docs/site.md`; questions for the client below
- [ ] Written permission from the client for the content and its scope
      (`docs/templates/crawl-permission.md` for a website)
- [ ] The client's content as Markdown in `CONTENT_DIR` (`title` and `url` front matter); for a
      website, crawled and cleaned ([website-content.md](website-content.md)), the cleaner
      adapted and checked against the live pages
- [ ] Content not from a website: `snapshot.py`, `clean.py`, `refresh.py`,
      `scripts/survey_site.py` and their tests deleted, and AD-24 superseded
- [ ] `content/` sample removed or kept out of the stack; `evals/site_titles.txt` refreshed
- [ ] `ALLOWED_LINK_PREFIX` and `brand.ts` `linkPrefix` set to the client's site
- [ ] `evals/retrieval.yaml` with the client's representative questions; `RETRIEVAL_MIN_SCORE`
      and `RETRIEVAL_MAX_GAP` measured with `scripts/verify_retrieval.py`
- [ ] Tools, if any, behind their seam with fakes (the `ai-project` skill's
      `references/tools-and-agents.md`)

**Done when:** the stack answers from the client's content, and the retrieval check passes.

### Phase 4 — Behaviour (rules, follow-ups, clarifying, citations, handoffs, scenarios)

- [ ] Safety rules on or off agreed with the client (`--safety-rules`, `SAFETY_RULES`); if off,
      recorded as an AD
- [ ] With safety rules on: the client's safety-critical situations in `rules.py`, every phrasing and look-alike in
      `tests/test_rules.py`; the fixed reply's wording approved by the client
      (`docs/templates/safety-wording-approval.md`)
- [ ] Prompts rewritten for the client's voice and services (tone and style guide)
- [ ] Scenarios for each category, against the client's content; run and compared

**Done when:** the scenarios run locally and every behaviour category is covered.

### Phase 5 — Cloud readiness (runtime image runs locally)

- [ ] `agentcore` Compose profile answers from an index published to `data/store`
- [ ] The local API calls it with `AGENT_RUNTIME=agentcore`
- [ ] For a website: `INDEX_STORE=data/store python -m assistant.refresh --seed <snapshot>`,
      then a second run refreshes with no change

**Done when:** the cloud image answers locally, through the API, as it will in the cloud.

### Phase 6 — Cloud test environment and evaluation

- [ ] Seams swapped one at a time: model, index store, prompts in Langfuse, guardrail, memory
- [ ] Evaluation baseline on the production model; latency targets agreed and measured

**Done when:** every scenario passes on the cloud, the eval baseline is recorded, the agreed
latency targets are met, and testers can use it.

## Later

- Production, CI/CD ([CI-CD.md](CI-CD.md) lists the manual steps a pipeline must take over),
  alarms, long-term memory.

## Resources made outside Terraform

| Resource | Account / region | Definition | Made by, date | Imported |
| --- | --- | --- | --- | --- |
| | | | | |

## Decisions needed

| ID | Question | Owner | Needed by |
| --- | --- | --- | --- |
| D1 | | | |
