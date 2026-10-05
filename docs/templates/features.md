<!--
Client-facing capability overview, read by the client and by Crystal Delta's executives.
Rules:
- Dot points, short. No prose paragraphs.
- Only features marked Live in docs/feature-register.md. Never overclaim.
- Features only: no commercial model, cost, pricing, open decisions, questions for the client or
  internal concerns (those go in PLAN.md).
- Keep the four sections; headings state the benefit.
- Figures (pages, tests, phrasings) are rechecked before each send.
Delete this comment when copying.
-->

# <Client> <assistant name>: capability overview

**Status:** Draft · **Updated:** <d Month yyyy>

## At a glance

- <What it does, for whom, from what content, in whose voice, with sources linked.>
- <Coverage: number of pages or documents, number of passages.>
- Safe by design: <safety-critical situations> are handled by fixed rules before any AI is
  involved, and every question and answer is checked by <guardrail service>.
- <Personalisation: region, customer type, …>
- Fully traceable: every answer can be inspected, step by step.
- Tested automatically on every change: <n> tests, including <n> safety phrasings and <n>
  scripted conversations.
- Runs on <cloud and service>, with content, wording and quality kept up to date without new
  releases.

## 1. Safe by design

### Independent layers of protection

1. **Rules before the AI:** <situations> are recognised by rules before the AI model sees the
   message.
2. **Content checks:** <guardrail service> checks every question and every answer.
3. **Grounded answers:** the AI answers only from <client>'s content.
4. **Controlled links:** only pages the assistant retrieved from <domain> become links.

### <Safety-critical situations> come first, and never depend on the AI

**What it recognises**

- <…>

**What the user is told**

- <The approved fixed reply, summarised.>

**Why it is dependable**

- **It expects typing under stress:** <examples of misspellings it catches>.
- **It errs on the side of safety:** a borderline message gets the safety response.
- **Everyday questions aren't caught:** <look-alike examples answered normally>.
- **It is instant:** no search and no AI call.
- **It is tested on every change:** <n> phrasings, and <n> look-alikes that must not trigger it.

### Content checks on every question and answer

- **Blocked:** <…>
- **Masked:** <…>
- **Scored:** each answer, for how well the content supports it.

### Knows its limits

- **Complaints:** <…>
- **Account requests:** <…>
- **Off-topic requests:** <…>

## 2. Accurate and personal

### Answers come only from <client>'s content

- **Sources:** <…>
- **No guessing:** if the content doesn't answer the question, the assistant says so.
- **Nothing invented:** no plans, prices, phone numbers or policies.
- **Every answer cites its pages,** linked to <domain>.

### <Personalisation>

- <…>

### Follows the conversation

- **Follow-up questions are understood in context.**
- **It asks one short question when the answer depends on the user.**

### <Client>'s voice

- **Follows <client>'s tone and style guide.**
- **Short answers suited to a chat window,** with steps as numbered lists.

## 3. Built for enterprise operation

### Architecture and privacy

- **Runs on <service>,** using <model family>.
- **The model is a setting:** it can be changed or upgraded without changing code.
- **Conversation links can't be guessed or forged.**
- **Personal details are masked** in the monitoring records.
- **Usage is capped** to protect against misuse.

### Fast, streamed answers

- **Progress shows at once,** and the answer streams as it is written.
- **The speed of every answer is recorded.**

### Every answer is traceable

- **The full path:** which rule applied, what was searched and found, what the AI wrote.
- **Content-check results** and **speed and usage** on every answer.

## 4. Operations and continuous improvement

### Content kept current

- <…>

### Controlled changes to the wording

- **Managed in Langfuse,** tried on a test copy, rolled back within a minute.
- **Safety wording is protected:** changed only with <client>'s approval; a version without
  <required text> is refused automatically.

### Continuous monitoring

- <…>

### Quality assurance on every change

- **<n> automated tests** and **<n> scripted conversations** run on every change.
