# Testing and evaluation

## Unit tests (no network, ever)

- `tests/conftest.py` sets `os.environ` **before** any package import, pinning every seam to its
  local or in-process value and blanking keys (`LANGFUSE_*`, `THREAD_ID_SECRET`, `INDEX_STORE`),
  so a developer's `.env` never leaks into the suite. Also `ORT_DISABLE_TELEMETRY=1`.
- `tests/fakes.py`: `FakeChatModel` (scripted replies, streams), `FakeRetriever`,
  `FakeGuardrail` (scripted decisions), `FakePrompts`, fake tools that record calls;
  `fake_components(**overrides)`; a `fakes` fixture that calls `service.use(...)` and resets.
- Change settings with `monkeypatch.setattr(config.settings, "name", value)`, never the
  environment (settings are read once at import).
- Must-have tests:
  - seams: each factory builds without network; every checkpointer serves sync **and** async;
  - streaming contract: the event order, `replace` after a block, sources after tokens;
  - the singleton: `get_agent()` on a worker thread with a timeout (catches a `Lock` deadlock);
  - rules: every safety phrasing and its look-alikes;
  - threads: forged and unsigned IDs refused;
  - API: status codes, unknown fields 422, limits;
  - every observation carries the session (tracing on a fake client).
- Lint: `ruff check . && ruff format --check .`; `npm run lint` (Biome).

## Behaviour scenarios (AD-20)

`evals/scenarios/<category>.yaml`: scripted conversations; each turn has the expected
`behaviour` (answer, clarify, emergency, handoff, decline, …), the sources it should cite,
`must`/`must_not` phrases, tool expectations, and an `ideal` answer in words. Categories that
worked: emergencies, follow-up, clarifying, complaints, account, out-of-scope, not-in-content,
audience-specific.

`scripts/run_scenarios.py` runs them through the running API (`--only <id>`, `-v`). Locally only
deterministic checks run: behaviour, cited sources, phrases, and **every number or price in the
answer appears in the cited content**. A behaviour change comes with a scenario. Compare runs;
do not demand 100% on the local model.

## Evaluation on the production model (Phase 6)

Use the `langfuse` skill (`create-dataset`, `judge-calibration`, `error-analysis`).

- Dataset: the scenarios plus 20–30 multi-turn answer-quality conversations with expected facts.
- LLM-as-judge on the production model family: faithfulness, relevance, completeness, clarity;
  calibrate against human labels before trusting it.
- `scripts/run_eval.py` replays, scores, compares with the previous run; the first scored run is
  the baseline, recorded with the content snapshot ID and model IDs.
- Re-run with the guardrail on to measure false positives and set thresholds.
- Latency targets agreed with the client (a starting proposal: feedback within 300 ms, first token
  within 2 s at the median), measured from traces.

## End-to-end check before claiming it works

`docker compose up -d --build`, `curl localhost:8000/api/health` (every dependency OK, points >
0), a multi-turn conversation with `scripts/chat.py` that streams with sources, and the trace in
Langfuse if tracing is on.
