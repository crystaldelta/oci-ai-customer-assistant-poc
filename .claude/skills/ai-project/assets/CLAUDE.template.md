# <project>

<One sentence: what the assistant does, for whom, from what.> Start with
[docs/PLAN.md](docs/PLAN.md) (the work plan, by phase). Design principles and the architecture
decision log are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): follow the principles, and record
any new architecture decision there. Setup and layout are in [README.md](README.md).

**Organisation style:** professional tone, Australian English and formats. Pricing or contract
questions go to Crystal Delta's Sales team.

## How the code fits together

- **One answering path.** `src/<pkg>/service.py` owns the graph: `route` (rules before any model
  call), <then the nodes>. `api.py` and `<runtime>.py` are thin adapters. Put answering logic in
  the service, not in a caller.
- **Seams.** The service never imports a provider. Each dependency has a module (`llm.py`,
  `retrieval.py`, `memory.py`, `guardrail.py`, `tools/`, `prompts/`, `tracing.py`) with a factory
  that reads `config.settings`. `service.build_components()` assembles them; tests replace them
  with `service.use(...)`.
- **Streaming contract.** `service.astream_answer()` yields `start` / `status` / `sources` /
  `token` / `replace` / `done` / `error`; `api.py`, `<runtime>.py`, `scripts/chat.py` and
  `frontend/src/api.ts` handle them. Adding an event means touching all of them.
- **Safety rules:** <the domain's safety-critical situations, the fixed replies, who approves>.
- **Thread IDs** are server-issued and signed (`threads.py`); the signed ID is a credential, never
  logged or passed to the service.
- <Add a bullet per subsystem as it is built: content pipeline, tools, prompts, tracing.>

## Pitfalls

- **Settings are read once, at import.** In tests use `monkeypatch.setattr(config.settings, ...)`.
- **Never `load_dotenv(..., override=True)`.** Container URLs come from Compose.
- **The singleton lock in `service.py` is an `RLock`; keep it that way.**
- **The stream matches on node names.** Give every model call its own node.
- **Read model output with `.text`, not `.content`.**
- <Add each new pitfall the day it is hit.>

## Testing

```bash
.venv/bin/pytest -q                            # no model, vector store or network
.venv/bin/ruff check . && .venv/bin/ruff format --check .
(cd frontend && npm run lint)
.venv/bin/python scripts/run_scenarios.py      # behaviour scenarios; needs the stack running
```

Before claiming a change works end to end: `docker compose up -d --build`, check
`curl localhost:8000/api/health`, then hold a multi-turn conversation with `scripts/chat.py` and
confirm answers stream with sources.
