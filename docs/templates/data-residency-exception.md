<!--
A time-limited exception to the data-residency principle, e.g. a model available only through a
cross-Region profile, or traces stored outside Australia. Get the client's agreement in writing,
then record it as an AD in ARCHITECTURE.md with the review date, and the open question in PLAN.md.
Delete this comment when copying.
-->

# Data residency exception: <service>

**Date:** <d Month yyyy> · **Review by:** <date or event, e.g. before real customers use it> ·
**Agreed by:** <client name, role> · **Recorded as:** AD-<nn>

## What leaves <Australia>

- **Service:** <e.g. model inference through a global cross-Region profile; Langfuse Cloud traces>
- **Data:** <e.g. questions and answers; masked traces>
- **Where it is processed or stored:** <regions>
- **Which environments:** <demo / test only>

## Why

<e.g. the chosen model is not offered in-region; the in-region alternative is blocked by …>

## What stays in <Australia>

<Runtime, content, memory, …>

## Controls

- <Public content and test questions only; no customer data.>
- <PII masked before traces leave the process.>
- <The model is a setting, so moving back in-region is a configuration change.>

## Exit

<What ends the exception, and the step to take: e.g. "Claude in-region once the entitlement is
fixed: set BEDROCK_MODEL_ID to …".>
