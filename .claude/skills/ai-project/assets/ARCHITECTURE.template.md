# <project>: architecture and design

**Owner:** <name> · **Started:** <d Mon yyyy> · **Updated:** <d Mon yyyy>

This document holds the design principles and the architecture decisions. The work plan and its
open questions are in [PLAN.md](PLAN.md); how to run the code is in the [README](../README.md).

**How to use it**

- **Principles** change rarely. A change that breaks one needs a decision recorded below.
- **Decisions** are numbered `AD-nn` and never renumbered or deleted. To change one, add a new
  decision and mark the old one `Superseded by AD-nn`.
- Open questions stay in PLAN.md under "Decisions needed" (`D1`, `D2`, …). When one is settled,
  record the outcome here and cross-reference the `D` number.
- Status here is `Proposed`, `Accepted` or `Superseded`; whether it is built is tracked in PLAN.md.

## Principles

<Copy from the ai-project skill and adapt.>

## Architecture

### Local

```
scripts/chat.py ─┐
frontend (Vite) ─┴─SSE──▶ api (FastAPI) ──▶ service ──▶ llm: Ollama on the host
                                              ├──▶ retriever / tools
                                              ├──▶ memory: SQLite
                                              ├──▶ guardrail: no-op
                                              ├──▶ prompts: Langfuse, repo files as fallback
                                              └──▶ tracing: Langfuse, PII masked (optional)
```

### Target (<cloud>, <region>)

<Diagram and a component table: component, runs on, started by, does.>

### Cloud environment (DevOps stages)

| Stage | Contents | Unblocks |
| --- | --- | --- |
| 0 — needed now | Account, model access, cost alert, developer access, Langfuse project, repo | Model on the cloud from a laptop |
| 1 — agent runtime | Registry, agent runtime, memory, permissions, logs | Deploying the agent |
| 2 — guardrail, testers | Guardrail, read-only tester access | Safety on the cloud, testing |
| 3 — hosted demo | Edge, static site, API host, cost stop, content job | Demos outside the team |

**Deliberately deferred:** <list, each needing its own decision before it is requested>.

## Decision log

| ID | Date | Area | Decision | Why | Status |
| --- | --- | --- | --- | --- | --- |
| AD-01 | | Cloud | | | |

### Adding a decision

Add a row with the next `AD-nn`, today's date and status `Proposed` or `Accepted`. When the
reasoning needs more than a table cell, add a section below the table headed with the ID.
