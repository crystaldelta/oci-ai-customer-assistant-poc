# ai-starter: architecture and design

**Owner:** <name> · **Started:** 5 Oct 2026 · **Updated:** 5 Oct 2026

This document holds the design principles and the architecture decisions. The work plan and its
open questions are in [PLAN.md](PLAN.md); how to run the code is in the [README](../README.md).

**How to use it**

- **Principles** change rarely. A change that breaks one needs a decision recorded below.
- **Decisions** are numbered `AD-nn` and never renumbered or deleted. To change one, add a new
  decision and mark the old one `Superseded by AD-nn`.
- Open questions stay in PLAN.md under "Decisions needed" (`D1`, `D2`, …). When one is settled,
  record the outcome here and cross-reference the `D` number.
- Status here is `Proposed`, `Accepted` or `Superseded`; whether it is built is tracked in PLAN.md.
- **A new project:** the decisions below came with the starter, marked *Inherited from the
  starter; confirm or supersede*. In Phase 0, go through them with the intake answers: mark each
  one you keep `Accepted` with the date, and supersede the rest with a new row.

---

## Principles

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
6. **Safety-critical replies are deterministic.** Rules before any model call, biased towards
   false positives, fixed wording approved by the client. Never let a model write them.
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

---

## Architecture

### Local

```
scripts/chat.py ─┐
frontend (Vite) ─┴─SSE──▶ api (FastAPI) ──▶ service ──▶ llm: Ollama on the host
                                              │
                                              ├──▶ retriever ──▶ Qdrant `content` collection
                                              ├──▶ memory: SQLite (`memory-data` volume, by thread key)
                                              ├──▶ guardrail: no-op (logs sizes)
                                              ├──▶ prompts: repo files (or Langfuse, files as fallback)
                                              └──▶ tracing: Langfuse, PII masked (optional)

ingest (one-shot) ──▶ fastembed (ONNX) ──▶ Qdrant   (from content/: Markdown with title and url)
```

Each `name:` is a seam (principle 3), chosen in `config.py`. Each turn runs one explicit graph
(AD-03):

```
route ──(emergency or decline: fixed reply)────────────────────▶ END
  │
  ├─(follow-up)─▶ rewrite ─▶ retrieve ─▶ generate ─▶ END
  └────────────────────────▶ retrieve ─(below the score floor: decline)─▶ END
```

### Target (cloud, region per the data-residency decision)

The starter is ready for AWS; Azure is a seam value with a clear error until it is built (the
`ai-project-azure` skill). On AWS (the `ai-project-aws` skill has the detail and the DevOps
requests):

```
Request path: each customer question
  browser ──▶ CloudFront (demo password)
                ├──▶ S3: the front end
                └──▶ /api: Lambda (`lambda` target): the API ──IAM──▶ AgentCore Runtime (`agentcore` target)
                                                                        │  the LangGraph agent
                                                                        ├──▶ Bedrock model (a setting)
                                                                        ├──▶ AgentCore Memory (Phase 6)
                                                                        ├──▶ Bedrock Guardrail (AD-18)
                                                                        ├──▶ Langfuse: traces and prompts (AD-10, AD-11)
                                                                        └──▶ reads the index store: LATEST.json and the index
                                                                                     ▲
Content path: on demand, or a scheduled job a project adds                           │ writes
  python -m assistant.publish (content/ → embedded index → S3) ──────────────────────┘
```

| Component | Runs on | Started by | Does |
| --- | --- | --- | --- |
| API | Lambda behind CloudFront | A question from the page | Checks the caller (signed thread IDs, limits) and passes the turn to the agent (AD-21) |
| Agent | AgentCore Runtime | The API, one invocation per turn | Searches the published index and makes the turn's model calls (AD-20) |
| Publish | A developer machine, or a job a project schedules | A content change | Builds the index from the Markdown and publishes it (AD-22) |

### Cloud environment (DevOps stages)

| Stage | Contents | Unblocks |
| --- | --- | --- |
| 0 — needed now | Account, model access, cost alert, developer access, Langfuse project, repo | Model on the cloud from a laptop |
| 1 — agent runtime | Registry, agent runtime, memory, permissions, logs | Deploying the agent |
| 2 — guardrail, testers | Guardrail, read-only tester access | Safety on the cloud, testing |
| 3 — hosted demo | Edge, static site, API host, cost stop, content job | Demos outside the team |

The request states needs and reasons only; naming and implementation are DevOps' call. Use
`docs/templates/devops-request.md`.

**Deliberately deferred:** <list, each needing its own decision before it is requested: for
example a WAF, private networking, long-term memory, a scheduled crawl, production, CI/CD>.

---

## Decision log

| ID | Date | Area | Decision | Why | Status |
| --- | --- | --- | --- | --- | --- |
| AD-01 | 5 Oct 2026 | Local model | Ollama running natively on the host (`gpt-oss:20b` by default, `qwen3:8b` on smaller machines); containers reach it at `host.docker.internal` | No API keys; Docker on macOS has no GPU access; local runs check function, not quality (principle 4) | Inherited from the starter; confirm or supersede |
| AD-02 | 5 Oct 2026 | Embeddings | fastembed (ONNX) with `BAAI/bge-base-en-v1.5` (768 dimensions); the image build fails if torch is installed. A published index records its embedding model, and the agent refuses one built with another | An image of hundreds of MB rather than several GB; the model and collection are dimension-locked, so changing the model means re-ingesting | Inherited from the starter; confirm or supersede |
| AD-03 | 5 Oct 2026 | Agent flow | LangGraph, an explicit graph: `route` (rules, then the input guardrail), `rewrite` (follow-ups only), `retrieve`, `generate`. The search runs before the model, so one model call writes each answer; every model call has its own node | Removes the model call a ReAct loop spends deciding to search; gives the safety rules a place to run first (principle 6) | Inherited from the starter; confirm or supersede |
| AD-04 | 5 Oct 2026 | Users | Anonymous visitors. Conversations are protected by server-issued, HMAC-signed thread IDs (`threads.py`); the signed ID is a credential, never logged; the service gets only the key | One visitor cannot choose or guess another's conversation. Signed-in users or tools acting on their data need an identity provider: supersede this | Inherited from the starter; confirm or supersede |
| AD-05 | 5 Oct 2026 | Abuse limits | In the service and API, in process: question length (422), turns per thread counted from memory (409), requests per minute per client address (429). `X-Forwarded-For` is never trusted; behind an edge, its own header (`CLIENT_ADDRESS_HEADER`) | Anonymous endpoints where every request costs tokens. Per replica and reset on restart: enough for a demo, not for production | Inherited from the starter; confirm or supersede |
| AD-06 | 5 Oct 2026 | Dependencies | `requirements.in` holds the direct dependencies; `requirements.txt` is a lock compiled with `uv pip compile --universal --python-version 3.13`, no hashes | Loose ranges break builds; `--universal` makes a lock compiled on macOS valid in the Linux image | Inherited from the starter; confirm or supersede |
| AD-07 | 5 Oct 2026 | Language | Python 3.13 for the agent, API and content pipeline; TypeScript for the browser | LangGraph and the cloud agent SDKs are Python-first; latency is dominated by model calls and retrieval | Inherited from the starter; confirm or supersede |
| AD-08 | 5 Oct 2026 | Front end | A small React + Vite page in `frontend/`, for local demos and the hosted demo. Vite proxies `/api`, so the API has no CORS settings. Every brand value is a `--brand-*` custom property at the top of `styles.css`, and the words are in `brand.ts`. No logo, fonts or images are copied from the client's site; a licensed font gets a free stand-in | The client sees their own look early, without copying assets they have not supplied | Inherited from the starter; confirm or supersede |
| AD-09 | 5 Oct 2026 | Test content | Unit tests use a synthetic corpus (`tests/fixtures/content/`, `example.invalid` URLs, `synthetic: true`), pinned in `tests/conftest.py`. The stack serves `content/`, a synthetic sample until the client's content replaces it | No client content in the tests; the stack answers on day one | Inherited from the starter; confirm or supersede |
| AD-10 | 5 Oct 2026 | Observability | Langfuse, SDK v4 (4.7 or later), on only when both keys are set. One client per process with `tracing.mask_pii`; each turn runs inside `tracing.attributes`, so every observation carries the session, tags and trace name. Locally the Compose `tracing` profile; in the cloud a project per environment | Traces, prompt versions, datasets and scores in one place; PII masked before it leaves the process (principle 7). Record the Langfuse region against the data-residency decision | Inherited from the starter; confirm or supersede |
| AD-11 | 5 Oct 2026 | Prompts | Prompts are edited in Langfuse prompt management; with `PROMPT_SOURCE=langfuse` the service serves the `PROMPT_LABEL` (`production`) version, cached. The repo files are the fallback when Langfuse is unreachable, its keys are unset, or a version fails `prompts.problem` (text code depends on, in `prompts.REQUIRED`). `pull_prompts.py` copies Langfuse into the files at each release (`versions.json`); `push_prompts.py` seeds it | Prompt changes without a release, while answering never depends on Langfuse (principle 8). See [prompt-management.md](prompt-management.md) | Inherited from the starter; confirm or supersede |
| AD-12 | 5 Oct 2026 | Behaviour rules | `route` applies `rules.classify` before any search or model call: a danger to life gets the fixed `emergency` reply (repeated once on the next turn), an instruction override the fixed `decline`, complaints and account requests are answered with the `handoff` prompt added. Messages are normalised (lower case, doubled letters collapsed, known misspellings). The emergency reply must keep `000` (`prompts.REQUIRED`); a project whose reply cites a page sets `rules.EMERGENCY_PAGE` and adds `[1]`. After the search, a first question (or a rewritten follow-up) whose best passage scores below `RETRIEVAL_MIN_SCORE` is declined. The starter's danger patterns and reply wording are placeholders: the client's situations and approved wording replace them | Safety-critical replies never depend on a model (principle 6); off-topic questions cost no model call | Inherited from the starter; confirm or supersede |
| AD-13 | 5 Oct 2026 | Follow-ups and clarifying | A follow-up is searched as a standalone question written by the `rewrite` node (one model call over the last three exchanges, as a transcript; never streamed or stored); `NONE` means no search. An answer that is one question citing nothing reports `clarify` | Follow-ups searched as typed find the wrong pages; one short model call fixes that | Inherited from the starter; confirm or supersede |
| AD-14 | 5 Oct 2026 | Citations | The service numbers the sources (`page`), the model cites "[1]", and the callers make the number a link. Only a source URL under `ALLOWED_LINK_PREFIX` (the client's site) is kept; the page checks its own prefix (`brand.ts`) too. A URL in the model's text is never linked. `sources` lists the cited pages only (every page when none is cited), after the answer | The model never writes a link; no link leaves the client's site (principle 5) | Inherited from the starter; confirm or supersede |
| AD-15 | 5 Oct 2026 | Retrieval | Top 5 (`RETRIEVAL_TOP_K`), at most one passage per page (`RETRIEVAL_MAX_PER_PAGE`; the search asks for four times as many to fill the places), and passages more than `RETRIEVAL_MAX_GAP` (0.10) below the best are left out. The floor (0.62) and gap were measured on the first project's content: re-measure with `scripts/verify_retrieval.py` | FAQ pages otherwise crowd out the page that answers; less input means a faster first token | Inherited from the starter; confirm or supersede |
| AD-16 | 5 Oct 2026 | Chunking | One chunk per heading section, split at paragraphs above 1,000 characters, its text starting with the heading path. Ingest drops a chunk whose body repeats an earlier one. A filter by user context (region, segment) is added as a payload field from the page's URL, title or headings (never body text), with a matching `Retriever.search` keyword | Retrieved chunks make sense on their own; filters stay deterministic | Inherited from the starter; confirm or supersede |
| AD-17 | 5 Oct 2026 | Evaluation | Behaviour scenarios in `evals/scenarios/` (YAML, one file per category): the expected behaviour, cited pages, required and forbidden phrases, and an `ideal` answer in words. Locally only deterministic checks run, plus that every number or price in an answer is in the cited content; quality is judged against `ideal` on the production model in Phase 6 | Comparable runs from day one; a behaviour change comes with a scenario | Inherited from the starter; confirm or supersede |
| AD-18 | 5 Oct 2026 | Guardrail | A guardrail seam applied separately from the model call: on the question after the rules (so the safety reply never depends on it), and on the answer with the passages as grounding source. A block after streaming sends `replace` and replaces the answer in memory too. A failed check allows the text. Logs and traces get the decision, types and scores, never the matched text. On AWS a Bedrock Guardrail from `deploy/guardrail.json`, at a published version | Defence in depth without making the model a dependency of safety. See [guardrail.md](guardrail.md) | Inherited from the starter; confirm or supersede |
| AD-19 | 5 Oct 2026 | Memory | A LangGraph checkpointer keyed by thread key: SQLite locally (`ThreadedSqliteSaver`, which serves the sync and async APIs), AgentCore Memory short-term in the cloud (Phase 6). No long-term memory | A conversational style needs short-term memory; long-term memory needs a privacy decision first | Inherited from the starter; confirm or supersede |
| AD-20 | 5 Oct 2026 | Agent image | One Dockerfile with targets from one base: `api` (local stack), `lambda` (the API on Lambda), `models` (the embedding model built in, a non-root user) and `agentcore` (the default: the AgentCore Runtime entry point on 8080, arm64). The AgentCore session is the conversation; its ID is the thread key's SHA-256, never the signed ID | The cloud entry point is a thin adapter, testable locally under the `agentcore` Compose profile | Inherited from the starter; confirm or supersede |
| AD-21 | 5 Oct 2026 | Where a turn runs; hosted demo | `agent_client.py`: `AGENT_RUNTIME=in_process` locally, `agentcore` by ARN in the cloud or by URL against the local container. For demos, CloudFront with a password function (`deploy/cloudfront-password.js`) serves the page from S3 and `/api/*` from the API on Lambda through a function URL with origin access control; the page sends the body's SHA-256 | The browser never calls the agent runtime; the API holds what a public endpoint needs | Inherited from the starter; confirm or supersede |
| AD-22 | 5 Oct 2026 | Published index | `python -m assistant.publish` builds an embedded Qdrant index from `CONTENT_DIR` and publishes it to `INDEX_STORE` (S3 or a folder): the content, the index, then `LATEST.json` last. A new index with under 90% of the published pages or points, or another embedding model, is held back (exit 2; `--force`). The agent downloads the index on its first search and checks for a newer one every `INDEX_CHECK_SECONDS` | No vector service to run in the cloud; a content change needs no redeploy | Inherited from the starter; confirm or supersede |
| AD-23 | 5 Oct 2026 | Content source | The starter's pipeline starts from Markdown files with `title` and `url` front matter. Getting them (a crawl and clean of the client's site, a CMS export, documents) is the project's Phase 3; a polite crawler and cleaner exist in the first project to copy when needed | Keeps the starter small and the content source a project decision, made with the client's written permission | Inherited from the starter; confirm or supersede |

### Adding a decision

Add a row with the next `AD-nn`, today's date and status `Proposed` or `Accepted`. When the
reasoning needs more than a table cell, add a section below the table headed with the ID.
