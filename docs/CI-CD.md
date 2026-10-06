# CI/CD checklist

Nothing deploys automatically at the start of a project: checks are run by hand, and DevOps
builds, pushes and deploys each image as the README describes. This is the list to work through
when pipelines are set up, so nothing done by hand is missed. Each item names the manual step it
replaces. Tick an item with the date and the pipeline that now does it.

## Checks on every pull request

- [ ] **Unit tests:** `.venv/bin/pytest -q`. They need no network, model, Qdrant or Langfuse:
      `tests/conftest.py` pins every seam to its local or fake implementation.
- [ ] **Python lint:** `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`.
- [ ] **Front-end lint and build:** `npm ci && npm run lint && npm run build` in `frontend/`.
- [ ] **Lock file in step:** re-run the `uv pip compile … --universal` command from the README's
      [Dependencies](../README.md#dependencies) section and fail if `requirements.txt` changes.
      Keep `--universal`, or a lock made on macOS can break the Linux image.
- [ ] **Never on a pull request:** requests to the client's systems or website, cloud model
      calls, or pushes to Langfuse.

## Prompt management (AD-11)

Today: [prompt-management.md, Releasing](prompt-management.md#releasing).

- [ ] **Sync job.** Daily and on demand: `scripts/pull_prompts.py`, then open or update a "Sync
      prompts from Langfuse" pull request when the files change. Someone reviews and merges it;
      for the safety replies, only with the client's approval.
- [ ] **Optional:** a Langfuse webhook on a `production` label change triggers the sync job.
- [ ] **Release gate.** The release pipeline runs `scripts/pull_prompts.py --check` before
      building, and stops if the repo files differ from Langfuse.
- [ ] **Keys.** The Langfuse keys of the project that serves production go in the CI secret
      store, read-only for prompts if the plan allows a scoped key.

## Image builds

Today: the README's [Cloud](../README.md#cloud) section, run by hand.

- [ ] **One Dockerfile, one target per runtime, `linux/arm64`:** `agentcore` (the default
      target) for the agent runtime, `lambda` (with `--provenance=false`) for the API behind
      CloudFront, `refresh` for the scheduled website refresh (a container task), and any job
      image a project adds on top of `models`. Lambda needs `--provenance=false` because it
      rejects the extra attestation manifest `buildx` adds by default.
- [ ] **Tags.** Tag every image with the commit SHA (`git rev-parse --short HEAD` at build
      time); never deploy `:latest`. Record the previous tag as the rollback.
- [ ] **Scan the images** on push (ECR basic scanning, or the organisation's scanner).

## Deploys

Today: done by hand by DevOps, in the console or with the CLI.

- [ ] **Order of a release:** the agent runtime first, then the API. A setting is enabled only
      after the image that understands it is deployed.
- [ ] **Content after code.** When a release changes how the index is searched (payload fields,
      filters), deploy the agent before republishing the index (`python -m assistant.publish`).
- [ ] **Smoke test after each deploy:** load the page; stream one question through the edge (a
      POST through CloudFront needs `x-amz-content-sha256`, the body's SHA-256); check that
      sources come back and that the trace appears in Langfuse with the timing scores.
- [ ] **Approval before production.** The test environment deploys from `main`; production
      waits for a manual approval.

## Environment settings as code

Today: set by hand on each runtime and function.

- [ ] **Agent runtime:** `LLM_PROVIDER`, `BEDROCK_MODEL_ID`, `INDEX_STORE`, `PROMPT_SOURCE` (and
      the other `PROMPT_*` if changed), `GUARDRAIL*`, `ALLOWED_LINK_PREFIX`, and the `LANGFUSE_*`
      keys from Secrets Manager.
- [ ] **API function:** `THREAD_ID_SECRET` from Secrets Manager, `AGENTCORE_RUNTIME_ARN`,
      `AGENTCORE_QUALIFIER`, `AWS_REGION`. The image sets `AGENT_RUNTIME` and
      `CLIENT_ADDRESS_HEADER`.
- [ ] **A record of each environment's settings,** in Terraform or a parameter file, so a new
      environment can be made from it.

## Infrastructure still set up by hand

- [ ] **Resources a developer made in the test account** from a definition in `deploy/` (the
      guardrail, the CloudFront password function): listed in PLAN.md and handed to DevOps to
      import into Terraform.
- [ ] **The content job,** if the project schedules one (a container task, not a function: its
      run time grows with the content), with alerts on a non-zero exit.

## Evaluation gates (they need a model and the content)

- [ ] **Retrieval:** `scripts/verify_retrieval.py` against the published index, before a content
      or retrieval change is promoted. It needs no model.
- [ ] **Behaviour:** `scripts/run_scenarios.py` on the production model, before a prompt, model
      or behaviour change is promoted; compare with the last run rather than requiring every
      scenario to pass.
- [ ] **Answer quality:** the Phase 6 Langfuse dataset and evaluation run, once they exist.

## Rollback

- [ ] Each service's previous image tag, recorded by the pipeline and redeployable in one step.
- [ ] **Prompts:** move the Langfuse `production` label back to an earlier version (no deploy),
      or set `PROMPT_SOURCE=file` to serve the image's files.
- [ ] **Content:** earlier indexes stay in the index store under `indexes/`; point
      `LATEST.json` back to the previous one. Write the step down, then automate it.
