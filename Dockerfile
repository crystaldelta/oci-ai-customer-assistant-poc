# Images from one base (AD-20):
#
#   api        the local stack's `api` and `ingest` services in docker-compose.yml.
#              In development Compose bind-mounts ./src over /app/src and uvicorn
#              --reload picks up host edits.
#   lambda     the API on AWS Lambda behind CloudFront, for a hosted demo (AD-21):
#              the `api` image with the Lambda Web Adapter, calling AgentCore. Build
#              it with --provenance=false (Lambda refuses a multi-platform index):
#                docker buildx build --platform linux/arm64 --provenance=false \
#                  --target lambda -t ai-starter-lambda .
#   models     the base with the embedding model built in and a user that is not root;
#              the base of `agentcore`, and of any scheduled content job a project adds.
#   agentcore  the AgentCore Runtime image (the default target): the entry point on
#              port 8080, with the embedding model built in. The content index is not:
#              it comes from the index store (INDEX_STORE) at run time. Build it for arm64
#              (README, "AWS"):
#                docker buildx build --platform linux/arm64 -t ai-starter-agentcore .
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    FASTEMBED_CACHE_PATH=/cache/fastembed \
    ORT_DISABLE_TELEMETRY=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

# Embeddings run on the ONNX runtime. Fail the build if torch comes back in
# through a transitive dependency: the image would still work, just several GB
# heavier, and nobody would notice.
RUN if pip show torch >/dev/null 2>&1; then \
      echo "ERROR: torch was installed; embeddings must use the ONNX runtime." >&2; \
      exit 1; \
    fi

# Baked in for standalone runs; bind-mounted over in development.
COPY src ./src


FROM base AS api

EXPOSE 8000

CMD ["uvicorn", "assistant.api:app", "--host", "0.0.0.0", "--port", "8000"]


FROM api AS lambda

# The adapter runs uvicorn as a Lambda function and streams the response, so the answer
# reaches the browser token by token through a function URL. The API only forwards turns to
# AgentCore here: no model, index or memory in this image's process.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter
ENV AWS_LWA_INVOKE_MODE=response_stream \
    AWS_LWA_READINESS_CHECK_PATH=/api/health \
    PORT=8000 \
    AGENT_RUNTIME=agentcore \
    CLIENT_ADDRESS_HEADER=CloudFront-Viewer-Address


FROM base AS models

# The runtime and any job may have no route to Hugging Face, so the embedding model travels in
# the image. Both run as a user that is not root; they write only to /tmp.
ENV FASTEMBED_CACHE_PATH=/app/models
RUN python -c "from assistant.retrieval import get_embeddings; get_embeddings()"
ENV HF_HUB_OFFLINE=1
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin agent


FROM models AS agentcore

# AgentCore has no Qdrant server, so the agent searches an embedded index: the one
# `assistant.publish` published to INDEX_STORE (set on the runtime), downloaded into /tmp on the
# first search and replaced when a newer one is published (AD-22).
ENV INDEX_CACHE_DIR=/tmp/assistant-index \
    LLM_PROVIDER=bedrock \
    MEMORY=in_process \
    DOCKER_CONTAINER=1

USER agent

EXPOSE 8080

CMD ["python", "-m", "assistant.agentcore"]
