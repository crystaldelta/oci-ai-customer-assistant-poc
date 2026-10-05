# AWS deployment reference

Proven on ea-customer-bot UAT (Oct 2026). Replace `<…>` placeholders. Run with the profile and
Region of the target account.

## Images

```bash
SHA=$(git rev-parse --short HEAD)
docker buildx build --platform linux/arm64 -t <pkg>-agentcore:$SHA .                                  # default target
docker buildx build --platform linux/arm64 --provenance=false --target lambda -t <pkg>-lambda:$SHA .
docker buildx build --platform linux/arm64 --target refresh -t <pkg>-refresh:$SHA .
aws ecr get-login-password | docker login --username AWS --password-stdin <acct>.dkr.ecr.<region>.amazonaws.com
docker tag … && docker push …
```

Before building the agent image: `scripts/pull_prompts.py`, commit the diff, `--check` exits 0.

## Dockerfile targets

- `lambda`: `FROM api`, copy `/lambda-adapter` from `public.ecr.aws/awsguru/aws-lambda-adapter`
  into `/opt/extensions/`, `AWS_LWA_INVOKE_MODE=response_stream`,
  `AWS_LWA_READINESS_CHECK_PATH=/api/health`, `PORT=8000`, `AGENT_RUNTIME=agentcore`,
  `CLIENT_ADDRESS_HEADER=CloudFront-Viewer-Address`.
- `agentcore`: `FROM models`, `LLM_PROVIDER=bedrock`, `MEMORY=in_process`,
  `INDEX_CACHE_DIR=/tmp/<pkg>-index`, non-root user, `EXPOSE 8080`.

## Agent runtime settings

`BEDROCK_MODEL_ID`, `AWS_REGION`, `INDEX_STORE=s3://<bucket>/content`, `PROMPT_SOURCE=langfuse`,
`LANGFUSE_PUBLIC_KEY` / `SECRET_KEY` / `HOST`, `GUARDRAIL=bedrock`, `GUARDRAIL_ID`,
`GUARDRAIL_VERSION`, limits.

Role permissions:

- `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` on the model; for an inference
  profile, the profile **and** the foundation model in every Region it routes to;
  Marketplace models also `aws-marketplace:ViewSubscriptions`, `aws-marketplace:Subscribe`.
- `bedrock:ApplyGuardrail` on the guardrail ARN.
- `s3:GetObject` on `content/LATEST.json` and `content/indexes/*` (no list, no write).
- AgentCore Memory permissions once memory moves there.

## Lambda (API)

Container function, arm64, 1024 MB, timeout 120 s, reserved concurrency ~5. Function URL:
`AWS_IAM`, `RESPONSE_STREAM`. Settings: `AGENTCORE_RUNTIME_ARN`, `AGENTCORE_QUALIFIER`
(`DEFAULT`), `AWS_REGION`, `THREAD_ID_SECRET` (Secrets Manager), `MAX_QUESTION_CHARS`,
`RATE_LIMIT_PER_MINUTE`. Role: `bedrock-agentcore:InvokeAgentRuntime` on the runtime.

## CloudFront

- Origins: S3 (origin path `/site`, OAC; bucket policy allows `site/*` only) and the function URL
  (OAC, sign always, so the viewer's `Authorization` header is never forwarded).
- `/api/*` behaviour: caching disabled, all methods, origin request policy forwarding
  `Content-Type`, `x-amz-content-sha256`, `CloudFront-Viewer-Address`.
- Viewer-request function (runtime 2.0) with a KeyValueStore for the password, on every behaviour.
- Custom domain: alternate domain name plus an ACM certificate in **us-east-1**.

## Guardrail

```bash
aws bedrock create-guardrail --cli-input-json file://deploy/guardrail.json \
  --tags key=project,value=<project> key=environment,value=<env>
aws bedrock-runtime apply-guardrail --guardrail-identifier <id> --guardrail-version DRAFT \
  --source INPUT --content '[{"text": {"text": "How do I pay my bill?"}}]'
aws bedrock create-guardrail-version --guardrail-identifier <id> --description "<what changed>"
aws bedrock update-guardrail --guardrail-identifier <id> --cli-input-json file://deploy/guardrail.json
```

Tags on the command, not in the file, so the same file serves `update-guardrail`. Test a fixed set
of messages (normal, safety-critical, angry, prompt attack, card number) on the draft before
publishing. Monitoring: Langfuse scores, plus CloudWatch `AWS/Bedrock/Guardrails` and a metric
filter on the agent's `check failed` warning.

## Content job (Fargate)

Task definition: arm64, ~2 vCPU / 4 GB, `INDEX_STORE`, `AWS_REGION`; role read/write on
`content/*` only. Own security group with outbound HTTPS; public IP or NAT (a NAT gives a fixed
address the client can allowlist). EventBridge Scheduler twice a day, off until the client
confirms. First run by hand with `--crawl` (or `--seed` from a local snapshot); `--force` after
a cleaner or chunker change. Exits: 0 published or unchanged, 1 refused by the site, 2 held back.

```bash
aws ecs run-task --cluster <cluster> --task-definition <td> --launch-type FARGATE \
  --network-configuration 'awsvpcConfiguration={subnets=[<subnet>],securityGroups=[<sg>],assignPublicIp=ENABLED}' \
  --overrides '{"containerOverrides":[{"name":"<container>","command":["python","-m","<pkg>.refresh","--force"]}]}'
```

## Smoke test after a deploy

Load the page; stream one question through CloudFront (with `x-amz-content-sha256`); sources come
back; the trace appears in Langfuse with timing scores and the session on every observation.

## Rollback

Previous image tag per service, recorded at deploy; prompts by moving the Langfuse label (or
`PROMPT_SOURCE=file`); content by pointing `content/LATEST.json` at the previous index.
