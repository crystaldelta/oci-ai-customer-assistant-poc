<!--
The staged request to DevOps for cloud resources. Send stage 0 on day one, so it is ready when
the code is. State needs and reasons only: naming and implementation are DevOps' call. Keep the
working copy where DevOps tracks work (e.g. a Slack canvas) and summarise it in ARCHITECTURE.md.
Delete this comment when copying.
-->

# <Project>: test environment request

**From:** <name> · **Date:** <d Month yyyy> · **Cloud and region:** <AWS ap-southeast-2 / Azure Australia East>

**What it is:** <one sentence>. **Who uses the test environment:** the delivery team, then named
testers, then demo recipients behind a password.

We develop locally while these are set up; each stage unblocks the next step, so earlier is
better. Anything we create ourselves in the test account is listed at the end for you to import
into Terraform.

## Stage 0: needed now

| Need | Why |
| --- | --- |
| A test account or subscription for this project | Isolation and cost tracking |
| Access to the model(s): <model IDs or deployments>, in <region> | First quality checks from a laptop. Please confirm which run in-region, and any Marketplace or quota step |
| A cost alert at <amount>/month | Early warning |
| Developer access (who: <names>) to call the model and create <listed test resources> | So development does not wait |
| A Langfuse project and keys (or confirmation we create it) | Traces and prompt management |
| A private Git repository: <org/name> | Code |

## Stage 1: agent runtime

| Need | Why |
| --- | --- |
| A container registry (<arm64> images) | Images built per commit |
| <Agent runtime service> | Hosts the agent |
| <Managed memory> | Conversation memory |
| A role for the agent with: <permissions> | Least privilege |
| Logs, and outbound access to Langfuse | Monitoring |

## Stage 2: safety and testers

| Need | Why |
| --- | --- |
| Permission for the agent to apply the guardrail (we can create the guardrail from `deploy/`) | Content checks |
| Read-only access for testers: <names> | Testing |

## Stage 3: hosted demo

| Need | Why |
| --- | --- |
| Edge (CDN) with a password, static site hosting, the API host | Demos outside the team |
| A scheduled job for the content refresh, with outbound HTTPS | Content kept current |
| A hard cost stop at <amount>/month | Bounded spend if the password leaks |
| Optional: a custom domain <name> | Presentation |

## Deliberately deferred

<Production, WAF, private networking, CI/CD, long-term memory, …: each needs its own decision.>

## Created by the team outside Terraform (to import)

| Resource | ID / ARN | Definition | Created | By |
| --- | --- | --- | --- | --- |
| | | `deploy/<file>` | | |
