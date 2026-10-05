# Langfuse project setup

How to prepare a Langfuse project for this assistant, once per environment (local, test,
production). Tracing and prompt management themselves are described in the README and
[prompt-management.md](prompt-management.md).

## 1. Create the project and keys

- **Local:** the Compose `tracing` profile creates a project with the keys `pk-lf-local` and
  `sk-lf-local` (`LANGFUSE_INIT_*` in `docker-compose.yml`).
- **Cloud:** create a project per environment in the organisation's Langfuse, and an API key pair
  for each (project settings, API keys). Note the region (`LANGFUSE_HOST`): Langfuse Cloud has no
  Australian region, so record where traces are stored as a decision.
- Keys go in `.env` locally and in the secret store in the cloud; never in the repo.

## 2. Run the setup script

```bash
.venv/bin/python scripts/setup_langfuse.py --dry-run   # what it would create
.venv/bin/python scripts/setup_langfuse.py
```

It reads the keys from the environment or `.env` and creates what
[`deploy/langfuse-setup.json`](../deploy/langfuse-setup.json) defines, only where missing (matched
by name), so it is safe to re-run:

- **Score configs** for the scores the code sends (`guardrail`, `guardrail_grounding`,
  `guardrail_relevance`, `first_status_ms`, `first_token_ms`, `total_ms`) and for people
  (`user_feedback`, `review`). They document each score in the UI and are what annotation queues
  use. The code sends scores by name, not by config ID, so a config does not validate them.
- **An "Assistant health" dashboard:** answer times, guardrail decisions, grounding, guardrail and
  model latency. It uses Langfuse's *unstable* dashboard API: the server does not validate field
  names when a widget is created, so open the dashboard once traces exist and check that each
  widget shows data.

To change what it creates, edit the JSON, not the script. An existing dashboard is left alone;
delete it in Langfuse to recreate it.

## 3. Seed the prompts

```bash
.venv/bin/python scripts/push_prompts.py --dry-run
.venv/bin/python scripts/push_prompts.py
```

Creates only the prompts the project lacks, labelled `production`. Then set
`PROMPT_SOURCE=langfuse` where the project should serve them.

## 4. By hand (no API)

- **Protect the `production` label** (project settings, prompt management), if the plan includes
  it, so only named people can promote a prompt.
- **Data retention** for traces, set deliberately.
- **Members** with the least role they need; reviewers as annotators.
- **Evaluators** (LLM-as-judge) once the evaluation is designed: see the `langfuse` skill's
  `judge-calibration` guide.

## 5. Check

Ask the assistant one question with tracing on, then in Langfuse:

- the trace has the session, tags and trace name on every observation;
- PII in the question is masked;
- the timing scores are on the trace;
- the generation links to its prompt version;
- the dashboard's answer-time widgets show the question.
