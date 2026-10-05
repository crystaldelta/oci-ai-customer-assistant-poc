# Prompt management

How the assistant's prompts are edited in Langfuse, tested, released and rolled back (AD-11).

## Where the prompts are

The prompts are edited in **Langfuse prompt management**, in the project the service reports its
traces to: in the cloud, the environment's project in Crystal Delta's Langfuse (see
[langfuse-setup.md](langfuse-setup.md)); locally, the `ai-starter` project of the `tracing`
Compose profile (<http://localhost:3000>, login in `.env.example`).

There are five text prompts, each named `ai-starter/<name>`:

| Prompt | Used for |
| --- | --- |
| `system` | Every written answer: tone, grounding and citations (follows the client's tone and style guide) |
| `handoff` | Added for complaints and account requests |
| `rewrite` | Turns a follow-up into a standalone search (never shown to the customer) |
| `emergency` | The fixed reply to a danger to life, with no model call |
| `decline` | The fixed reply to off-topic or manipulative questions, and to questions the content does not match |

A project adds its own (a prompt added to the system message for a customer segment, a fixed
reply for a second safety situation): a file in `src/assistant/prompts/`, its name in
`prompts.NAMES`, any text code depends on in `prompts.REQUIRED`, then `push_prompts.py`.

The service serves the version labelled **`production`** (the `PROMPT_LABEL` setting). A change
reaches answers within `PROMPT_CACHE_SECONDS` (60) of moving the label, with no release.

The files in `src/assistant/prompts/` are the **fallback**: the service uses them whenever
Langfuse cannot be reached, its keys are not set, `PROMPT_SOURCE=file`, or a version fails the
checks below. They are copied from Langfuse at each release (see [Releasing](#releasing)).

## What a prompt must keep

The service checks every version it fetches. A version that fails is not used: the service logs
a warning naming the prompt and version, and serves the repo file instead.

| Prompt | Must contain | Why |
| --- | --- | --- |
| every prompt | some text | An empty prompt would remove the instructions |
| `rewrite` | `NONE` | The model's answer when a message needs no search |
| `emergency` | `000` | The emergency number (AD-12). Add `[1]` when `rules.EMERGENCY_PAGE` gives the reply a page to cite, and the local number for a service outside Australia |

A placeholder the service fills goes in `REQUIRED` too. Write it as `{region}`, with single
braces; Langfuse's own `{{variables}}` are not used.

**The emergency reply is safety advice.** The starter's wording is a placeholder: the client
approves the real wording before the first demo (see `docs/templates/safety-wording-approval.md`).
Any change needs the client's approval before it is labelled `production`, and the checks above
protect only the number, not the wording. Never use a model to write or change it.

## Editing a prompt

1. Open the prompt in Langfuse and create a new version. Leave **Set the "production" label**
   unticked; add the `staging` label instead.
2. Try it on a local stack that serves `staging`: in `.env`, set `PROMPT_SOURCE=langfuse`,
   `PROMPT_LABEL=staging` and the keys of the project you edited. Then hold a conversation
   with `scripts/chat.py` that exercises the prompt. For a change to how answers are written,
   also run `.venv/bin/python scripts/run_scenarios.py` and compare with the last run.
3. Promote it: in Langfuse, add the `production` label to the new version. Answers use it
   within a minute.
4. Check one answer in Langfuse: the generation's prompt link shows the version, and the
   prompt's page lists the answers that used it.

Each trace records the prompts its turn used, with their versions: on the generation (linked to
the `system` and `rewrite` prompts), in the node outputs (`prompts`), and in the service log
(`answered … prompts=system:7,handoff:2`, or `system:file` for a fallback).

## Rolling back

- **One prompt:** in Langfuse, move the `production` label back to the earlier version. This
  applies within a minute.
- **All prompts:** set `PROMPT_SOURCE=file` on the runtime and update it. The service then
  serves the repo files from the image.

## Releasing

Langfuse edits reach the repo by hand, at the start of each release, before the images are
built. Until there is a pipeline (see [CI-CD.md](CI-CD.md)), whoever builds the images:

1. Runs `.venv/bin/python scripts/pull_prompts.py`, with the keys of the project that serves
   production in `.env`. It writes each `production` version to its file, records the version
   numbers in `src/assistant/prompts/versions.json`, and refuses any version that fails
   the checks.
2. Reviews `git diff src/assistant/prompts/`. These are the Langfuse edits since the
   last release.
3. Commits the result ("Sync prompts from Langfuse, <date>").
4. Builds and pushes the images as usual.

`scripts/pull_prompts.py --check` writes nothing and exits 1 when the files differ from
Langfuse. The README's image build steps start with it.

A skipped sync does not change answers. The image's fallback is older than Langfuse, and the
service reports this: it logs `prompt 'system': Langfuse v9, the image's fallback is v7` once.
Locally, `/api/health` also lists the prompt under `prompts.stale`. In the cloud the health check
is the API's own, so the agent's log is the place to look.

## Adding or seeding prompts

`scripts/push_prompts.py` creates the prompts Langfuse lacks, from the repo files, labelled
`production`, and records their versions in `versions.json` (commit it). Run it:

- the first time, for each Langfuse project;
- when code adds a prompt (a new name in `prompts.NAMES`).

It never replaces a prompt that already exists, because the Langfuse version is usually the
newer edit. For a reviewed repo change that code depends on (a new placeholder, say),
`--force <name>` replaces it with a new version after showing the difference. `--dry-run` shows
what would happen.

## Settings

| Variable | Default | Notes |
| --- | --- | --- |
| `PROMPT_SOURCE` | `file` | `langfuse` serves Langfuse, with the files as fallback; it needs the `LANGFUSE_*` keys, without which the files are used |
| `PROMPT_LABEL` | `production` | The label served; `staging` to try an edit |
| `PROMPT_CACHE_SECONDS` | `60` | How long a fetched prompt is served before it is refreshed in the background |
| `PROMPT_TIMEOUT_SECONDS` | `2` | The wait for Langfuse before using the file |
| `PROMPT_RETRY_SECONDS` | `60` | After a failure, the files are used for this long before Langfuse is tried again |

When Langfuse is unreachable, a prompt already fetched keeps being served from the cache. A
prompt not yet fetched, such as one requested just after a restart, costs one wait of
`PROMPT_TIMEOUT_SECONDS`, after which every prompt comes from the files for
`PROMPT_RETRY_SECONDS`. Each answer's log line shows which prompts came from the files
(`system:file`). Locally, `/api/health` lists them under `prompts.fallback`.
