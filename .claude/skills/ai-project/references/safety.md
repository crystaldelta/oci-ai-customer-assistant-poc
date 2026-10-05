# Safety: rules first, guardrails, grounding, citations, PII

## Layers (in this order, independent of each other)

1. **Rules before the model** (`rules.py`, run in `route`): safety-critical situations, faults,
   off-topic and instruction-override attempts, out-of-area users, complaints and account
   requests. No search or model call for a fixed reply.
2. **Content guardrail** (`guardrail.py` seam): on the question after the rules, and on the
   answer against the content it was written from.
3. **Grounding:** answers only from retrieved content or tool results; a score floor declines
   off-topic first questions.
4. **Controlled links:** only URLs from the retrieved sources become links, and only on the
   client's domain.

## Safety-critical replies

- **Optional per project, always asked at intake.** Off only by a recorded decision with the
  client (`SAFETY_RULES=false` in the starter; the mechanism stays and stays tested).
- Detected by rules, biased towards false positives. Match normalised text: lower case, doubled
  letters collapsed ("smell" → "smel"), known misspellings and one-key typos (e.g. "has" for
  "gas" next to a smell word).
- Fixed wording, approved by the client. It carries the emergency number and one approved route
  (in the energy project: 000, and the client's distributor-finder page as the one cited source).
  Never a contact number the model writes.
- Comes before every other rule and the guardrail, applies in every region, and repeats once if
  the user hesitates on the next turn.
- Test every phrasing (`tests/test_rules.py`) plus look-alikes that must **not** trigger it
  ("gas bill", "a shock from my bill").
- Wording may live in prompt management, but code checks required text (`prompts.REQUIRED`:
  e.g. `000` and `[1]`) and refuses a version without it.

## Guardrail placement (lessons from AD-37)

- Apply the guardrail **separately from the model call** (an "apply guardrail" API), so the model
  stays a setting.
- Question check **after** the rules, so safety replies never depend on it. Fixed replies skip the
  answer check.
- Answer check once the answer is complete, with the passages as the grounding source and the
  search as the query. A block after streaming sends `replace` and replaces the answer in memory
  too.
- Block only the clear-cut at first (harmful content, prompt attacks, card numbers and passwords);
  everything else in **detect mode** until the evaluation measures false positives. Angry
  customers swear: do not block profanity on questions.
- Mask card details and passwords in questions before the model sees them or memory stores them.
- **Fail open:** a failed check (timeout, throttle, permission) allows the text and logs a
  warning; alert on that warning.
- Log and trace the decision, the types found and the scores, **never the matched text**.
- The definition lives in `deploy/` as the cloud CLI's input file, versioned; a published version
  number is a runtime setting. Never run the draft.

## Citations

- The service numbers the sources; the model cites "[1]"; callers turn the number into a link from
  the source's `url`. A URL in the model's own text shows as plain text.
- `sources` lists only the cited pages (every page when none is cited), sent after the answer.
- Number checks in the scenarios: every phone number and price in an answer must appear in the
  cited content.

## Handoffs and declines

- Complaints and account requests: acknowledge, give the official route (from the content), never
  argue, guess or promise. Never ask for account numbers or personal details unless the project
  is authenticated and the tool needs them.
- Off-topic and competitor comparisons: a fixed `decline` with an offer to help.
- Pricing or contract questions about Crystal Delta's services go to Sales.

## PII

- `tracing.mask_pii` on the Langfuse client masks account numbers, phone numbers, emails and
  street addresses in every input, output and metadata (recursive over dicts, lists and pydantic
  objects). Bias towards masking too much; leave the client's public service numbers alone.
- In place before any tester uses a cloud environment.
- Logs carry sizes, types and IDs, never message text.

## Abuse and cost

- Question length, turns per thread, requests per minute (see `architecture.md`).
- At the edge: a demo password (or real sign-in) and a hard cost stop (budget action, reserved
  concurrency).
- Tools that cost money or change data: per-turn call caps and confirmation (see
  `tools-and-agents.md`).
