# Observability (Langfuse) and prompt management

Reference: ea-customer-bot `tracing.py`, `prompts/__init__.py`, `scripts/push_prompts.py`,
`scripts/pull_prompts.py`, `docs/prompt-management.md`; AD-10, AD-36. For Langfuse API and
dataset work, use the `langfuse` skill.

## Tracing

- On only when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set. SDK v4 (4.7+).
- One client per process, created with `mask=tracing.mask_pii` **before** the first
  `CallbackHandler` (the handler finds the client by its public key).
- Each turn runs inside `tracing.attributes(...)` (`propagate_attributes`): session ID (the thread
  key), user ID if any, tags, and trace name on **every** observation. Without it the async
  stream sets them on the root only, and generations (which carry the cost) lose the session.
- The trace name comes from the `langfuse_trace_name` metadata key in v4.
- Custom observations (guardrail checks, tool calls) attach under their node: on the stream the
  node's span is not the current one, so find it through the handler's run map
  (`handler._runs[parent_run_id]`, see `tracing.observe_guardrail`).
- Scores per turn: `first_status_ms`, `first_token_ms`, `total_ms`; guardrail decision
  (categorical) and grounding scores; later user feedback.
- Logs: one `answered` line per turn with thread key, behaviour, timings, prompt versions and
  guardrail types. Never the message text or the signed ID.
- Local: the Compose `tracing` profile; Cloud: a project per environment. Note the Langfuse
  region against the data-residency decision.

## Prompts

- Prompts are Markdown files in `src/<pkg>/prompts/` **and** in Langfuse.
- `PROMPT_SOURCE=langfuse` serves the `PROMPT_LABEL` (`production`) version, cached
  (`PROMPT_CACHE_SECONDS`); the files are the fallback when Langfuse is unreachable, keys are
  unset, or a version fails the checks.
- `prompts.REQUIRED`: text code depends on (placeholders, sentinels like `NONE`, the emergency
  number and citation in safety replies). A version without it is refused.
- Short timeout (2 s); after a failure use the files for 60 s, so an outage costs one wait a
  minute.
- Each turn records the prompts used with versions (state, log, trace); each model call is
  linked to its prompt (`linked(prompt)`).
- `scripts/push_prompts.py` seeds the prompts Langfuse lacks (`--force <name>` to overwrite);
  `scripts/pull_prompts.py` copies Langfuse into the files at each release and records versions in
  `versions.json`; `--check` exits 1 when they differ (release gate). Health reports `stale`
  prompts.
- A prompt edit in the repo alone does not reach the cloud: edit in Langfuse, or push it.
- Test edits on a `staging` label against a local stack; roll back by moving the label.
- Protect the `production` label so only named people can promote.
- Safety replies change only with the client's approval; a model never writes them.
