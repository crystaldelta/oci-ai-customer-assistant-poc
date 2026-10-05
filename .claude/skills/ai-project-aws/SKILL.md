---
name: ai-project-aws
description: The AWS side of Crystal Delta's AI project blueprint, proven on ea-customer-bot. Covers Amazon Bedrock (Converse models, Guardrails), Bedrock AgentCore Runtime and Memory, Lambda behind CloudFront for the API and demo page, S3 index store, ECS Fargate scheduled jobs, IAM, ECR, staged DevOps requests and Terraform hand-over, all while developing locally without waiting for DevOps. Use whenever a new or existing AI, agent, chatbot or RAG project targets AWS, Bedrock or AgentCore, or when deploying, configuring or debugging one there. Read the ai-project skill first; this skill holds only the AWS specifics.
---

# AI project on AWS

Read `~/.claude/skills/ai-project/SKILL.md` first: the service, seams, safety and testing
patterns are cloud-neutral. This skill maps them to AWS. Reference implementation:
`~/Projects/ea-customer-bot` (README "AWS", `docs/CI-CD.md`, `docs/guardrail.md`,
`docs/content-refresh-runbook.md`, AD-27 to AD-31 and AD-37).

Deployment details, IAM and commands: `references/deploy.md`.

## Service map

| Seam / component | Local | AWS | Notes |
| --- | --- | --- | --- |
| `llm` | Ollama | Bedrock `ChatBedrockConverse` (`langchain-aws`) | `BEDROCK_MODEL_ID` setting: any Converse model or inference profile |
| `retriever` | Qdrant server | Embedded Qdrant index from S3 (`INDEX_STORE=s3://…`) | Bedrock Knowledge Base kept as an option behind the seam; only when content outgrows the embedded index |
| `memory` | SQLite checkpointer | In-process on the AgentCore session, then AgentCore Memory (`langgraph-checkpoint-aws`) | Short-term, ≤ 7 days |
| `guardrail` | No-op | Bedrock Guardrail via `ApplyGuardrail` | Definition in `deploy/guardrail.json` |
| `tools` | Stubs | Direct clients, or AgentCore Gateway (MCP) with AgentCore Identity | Not yet proven |
| `prompts`, `tracing` | Files / local Langfuse | Langfuse Cloud | Keys on the agent runtime only |
| Agent | `api` container | **AgentCore Runtime** (`agentcore` image, arm64, port 8080) | `GET /ping`, `POST /invocations` |
| API | `api` container | **Lambda** (container image + Lambda Web Adapter, response streaming) behind **CloudFront** | Signed thread IDs and limits live here |
| Front end | Vite dev server | S3 (`site/` prefix) behind CloudFront | One origin, so no CORS |
| Content job | `python -m <pkg>.refresh` | **ECS Fargate** task on an **EventBridge Scheduler** rule | Not Lambda: run time grows with content |
| Secrets | `.env` | Secrets Manager (or encrypted environment variables) | `THREAD_ID_SECRET`, Langfuse keys |

Region: `ap-southeast-2` (Sydney) by default.

## Request and content paths

```
browser ─▶ CloudFront (demo password function) ─┬─▶ S3 site/
                                                └─▶ /api/*: Lambda function URL (OAC, IAM) ──▶ AgentCore Runtime
                                                                                               ├─▶ Bedrock model
                                                                                               ├─▶ Bedrock Guardrail
                                                                                               ├─▶ Langfuse Cloud
                                                                                               └─▶ S3 content/LATEST.json + index
EventBridge Scheduler ─▶ Fargate refresh task ─▶ S3 content/ (snapshot, index, LATEST.json last)
```

## Local first: what a developer does without DevOps

1. **Day one:** everything local (Ollama, Qdrant, SQLite, no-op guardrail, local Langfuse).
   Request DevOps stage 0 the same day.
2. **With model access only** (a developer profile): `LLM_PROVIDER=bedrock` and `AWS_PROFILE` in
   `.env` runs the local stack against Bedrock. This is the first quality check.
3. **The AgentCore image locally:** the Compose `agentcore` profile runs the runtime image on 8080
   with `~/.aws` mounted read-only and a local index store (`data/store`), and
   `AGENT_RUNTIME=agentcore` sends the local API's turns through it, exactly as in the cloud.
4. **Resources a developer may create** in the test account with an admin profile: the guardrail
   (`aws bedrock create-guardrail --cli-input-json file://deploy/guardrail.json`), an inline IAM
   policy for it, a published index (`refresh --seed`). Keep each as a definition file in
   `deploy/`, record it in PLAN.md as "outside Terraform", and hand DevOps the file to import (the
   `terraform` plugin's `terraform-search-import` skill helps). Otherwise an apply may remove it.
5. **DevOps stages** (request needs and reasons only; naming is DevOps' call): 0 account, model
   access, budget alert, developer access, Langfuse project, repo; 1 ECR (arm64), AgentCore
   Runtime and Memory, roles, logs; 2 guardrail permission, tester access; 3 CloudFront, S3, Lambda,
   Fargate job, budget action, optional domain.

## AWS lessons (each cost time)

- **AgentCore contract:** arm64, port 8080, `/ping` and `/invocations` (`BedrockAgentCoreApp`). A
  FastAPI app on 8000 fails deployment. The session ID (33+ characters) is the conversation;
  send the SHA-256 of the thread key, never the signed ID. Sessions end after 15 minutes idle, so
  in-process memory lasts a conversation only until then.
- **Models:** the model ID is a setting; DevOps once had to change models because an Anthropic
  model was blocked by its **Marketplace entitlement** (the role needs
  `aws-marketplace:ViewSubscriptions` and `Subscribe` for the first call). `global.` and `us.`
  inference profiles process **outside Australia**: allowed only by a dated, time-limited
  decision. Some models (OpenAI on Bedrock) refuse a temperature: leave it unset. Converse
  returns content blocks including reasoning: read `.text`.
- **Guardrail:** Classic tier in-Region (Standard tier needs a cross-Region profile). Apply with
  `ApplyGuardrail`, not on the model call. Runtime uses a numbered version, never `DRAFT`. Grounding
  limits: source ≤ 100,000 characters, query ≤ 1,000, guarded text ≤ 5,000. No Australian PII
  types; custom regexes for account numbers also catch 13/1300/1800 numbers. Without
  `bedrock:ApplyGuardrail` every check fails open with `AccessDeniedException` in the log.
- **Lambda API:** build with `--provenance=false` (Lambda refuses a multi-platform index). Function
  URL with `AWS_IAM` auth and `RESPONSE_STREAM`; CloudFront reaches it by origin access control,
  and a POST needs the page to send `x-amz-content-sha256` (CloudFront cannot hash the body).
  Read the client address from `CloudFront-Viewer-Address`.
- **Demo protection:** a CloudFront Function with a KeyValueStore for the password; reserved
  concurrency (~5); a budget action that attaches deny-all to the Lambda's role.
- **S3 bucket:** one bucket, `site/` (served) and `content/` (never served); deploy the page with
  `aws s3 sync … s3://<bucket>/site/ --delete`, never to the root. Versioning on; lifecycle on
  noncurrent versions only (an age rule on `content/` would delete the live index).
- **Fargate job:** the default VPC's default security group has no outbound rule, so the task
  fails to start: give it its own group with outbound HTTPS and a public IP (or NAT). Timeout ≥ 1
  hour. Alert on exits 1 and 2 and `TaskFailedToStart`.
- **Images:** tag with the commit SHA; deploy the image before enabling a setting it introduces;
  order of a release: agent runtime, then Lambda, then the job's task definition.
- **Langfuse from AgentCore:** traces need a route to Langfuse; check traces are not duplicated
  by AgentCore's own observability, and that masking applies. A host Python may need
  `SSL_CERT_FILE` set to certifi's bundle.
- **Terraform:** DevOps owns it. Anything made by hand is listed in PLAN.md with its ARN, for
  import.
