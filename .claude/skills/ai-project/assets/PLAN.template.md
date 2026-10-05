# <project>: work plan

**Owner:** <name> · **Updated:** <d Mon yyyy>

Design and decisions: [ARCHITECTURE.md](ARCHITECTURE.md). Tick an item with the date and the
evidence it works (what was run, what it showed).

## Phases

### Phase 0 — Setup

- [ ] Intake answers recorded; unknowns listed under Decisions needed
- [ ] `CLAUDE.md`, `ARCHITECTURE.md` (principles), README, `.env.example`
- [ ] DevOps stage 0 requested (needs and reasons only; naming is DevOps' call)

**Done when:** the repo exists and the stage 0 request is with DevOps.

### Phase 1 — Foundations

- [ ] Service with a minimal graph; API with streaming; signed thread IDs; limits
- [ ] Compose stack; `scripts/chat.py`; front-end page in the client's colours
- [ ] Tests with fakes; ruff; Biome

**Done when:** a local multi-turn conversation streams, and the tests pass with no network.

### Phase 2 — Seams for every cloud dependency

| Seam | Local | Cloud | Setting |
| --- | --- | --- | --- |
| `llm` | Ollama | | `LLM_PROVIDER` |
| `retriever` | Qdrant + ONNX | | `RETRIEVER` |
| `memory` | SQLite | | `MEMORY` |
| `guardrail` | No-op | | `GUARDRAIL` |
| `tools` | Stubs | | `TOOLS_*` |
| `prompts` | Files / local Langfuse | Langfuse, files as fallback | `PROMPT_SOURCE` |
| `tracing` | Off / local Langfuse | Langfuse | `LANGFUSE_*` |

**Done when:** swapping a provider is a settings change only; PII masking and timings are in.

### Phase 3 — Content pipeline / tools
### Phase 4 — Behaviour (rules, follow-ups, clarifying, citations, handoffs, scenarios)
### Phase 5 — Cloud readiness (runtime image runs locally)
### Phase 6 — Cloud test environment and evaluation

**Done when:** every scenario passes on the cloud, the eval baseline is recorded, the agreed
latency targets are met, and testers can use it.

## Later

## Decisions needed

| ID | Question | Owner | Needed by |
| --- | --- | --- | --- |
| D1 | | | |
