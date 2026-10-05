# Feature register

**Updated:** <d Month yyyy>

The internal record behind [features.md](features.md). One row per feature a client would
recognise (not per task). Only `Live` rows appear in `features.md`, worded as there.

**Status:** `Planned` → `Built` (works locally, with evidence) → `Live` (runs in the environment
the client sees) · `Dropped` (keep the row, say why).

**Evidence:** a commit, the date and what was checked ("scenarios 31/31 on Bedrock", "trace shows
masked email"). A `Live` row also names the environment and image or version.

| ID | Feature (client wording) | Section | Status | Evidence | In features.md | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| F-01 | Safety-critical messages get a fixed, approved reply before any AI is involved | 1 Safe by design | Planned | | No | Wording needs client approval |
| F-02 | Every question and answer is checked by <guardrail service> | 1 Safe by design | Planned | | No | |
| F-03 | Answers only from <client>'s content, with each source linked | 2 Accurate and personal | Planned | | No | |
| F-04 | Follow-up questions understood in context | 2 Accurate and personal | Planned | | No | |
| F-05 | Answers stream as they are written | 3 Built for enterprise | Planned | | No | |
| F-06 | Every answer traceable, with personal details masked | 3 Built for enterprise | Planned | | No | |
| F-07 | Wording managed in Langfuse, with rollback | 4 Operations | Planned | | No | |
| F-08 | Automated tests and scripted conversations on every change | 4 Operations | Planned | | No | |

## Figures quoted in features.md

Recheck before each send.

| Figure | Value | How to get it | Checked |
| --- | --- | --- | --- |
| Pages / passages | | `/api/health` (points), the content manifest | |
| Tests | | `.venv/bin/pytest -q` (count of passed) | |
| Safety phrasings / look-alikes | | `tests/test_rules.py` | |
| Scripted conversations | | `evals/scenarios/*.yaml` | |
