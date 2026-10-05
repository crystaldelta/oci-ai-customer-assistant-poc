---
name: ai-project-azure
description: The Azure side of Crystal Delta's AI project blueprint - a bare-bones starting map (Azure OpenAI / AI Foundry models, Azure AI Content Safety, Container Apps and Container Apps Jobs, Blob Storage, Key Vault, managed identities, Entra ID, Front Door, Terraform azurerm), local-first so development starts without waiting for DevOps. Use whenever a new or existing AI, agent, chatbot or RAG project targets Azure, Azure OpenAI or AI Foundry. Read the ai-project skill first; this skill holds only the Azure specifics. Not yet proven on a project - verify each choice against current Azure docs and refine it on the first real one.
---

# AI project on Azure (bare bones)

Read `~/.claude/skills/ai-project/SKILL.md` first. This skill only maps the blueprint to Azure.
**Nothing here has been built yet.** At project start, check every row against current Azure
documentation (regional availability in Australia East especially), record the choices as ADs,
and update this file afterwards with what was learnt. The AWS skill (`ai-project-aws`) shows the
level of detail to aim for.

## Service map (starting proposal)

| Seam / component | Local | Azure | Check at project start |
| --- | --- | --- | --- |
| `llm` | Ollama | Azure OpenAI in AI Foundry: `AzureChatOpenAI` (`langchain-openai`), deployment name as a setting (`AZURE_OPENAI_DEPLOYMENT`), Entra ID auth via `DefaultAzureCredential` | Model availability in the Region; deployment type: **Global** and **Data Zone** process outside Australia (the equivalent of AWS cross-Region profiles), **Standard (regional)** stays in-Region |
| `retriever` | Qdrant server | Embedded Qdrant index from Blob Storage (same `LATEST.json` pattern as AWS), or Azure AI Search | AI Search adds an always-on cost; keep the embedded index unless the content outgrows it |
| `memory` | SQLite | A LangGraph checkpointer on Azure Database for PostgreSQL (`langgraph-checkpoint-postgres`) or Cosmos DB | Expiry for short-term memory |
| `guardrail` | No-op | Azure AI Content Safety: Prompt Shields (questions), text moderation, groundedness detection (answers), called separately from the model | Groundedness detection availability in the Region; Azure OpenAI also applies its **own content filter** on every call, which raises a content-filter error: handle that as a block |
| `tools` | Stubs | Direct clients with managed identity; Azure API Management in front of client systems | |
| `prompts`, `tracing` | Files / local Langfuse | Langfuse Cloud | Langfuse region against data residency |
| Agent + API | `api` container | **Azure Container Apps**: the API and agent can be one app at first (SSE streaming, scale to zero); split later if needed | Request timeout for long streams; ingress settings |
| Front end | Vite | Static Web Apps, or Blob static website behind Front Door | |
| Content job | `refresh` module | **Container Apps Job** on a cron schedule | |
| Secrets, identity | `.env` | Key Vault; user-assigned managed identity per app; Entra ID for signed-in users | |
| Edge, demo protection | none | Front Door (+ WAF), or Container Apps built-in authentication (Entra) for demos | |
| Logs | stdout | Log Analytics / Application Insights | |
| IaC | | Terraform `azurerm` (+ `azapi` for new resources), owned by DevOps | |

Default Region: **Australia East**.

## Local first

- Everything runs locally as in the core skill; no Azure account needed to start.
- **Azurite** (Compose service) emulates Blob Storage for the index store.
- With model access only: `LLM_PROVIDER=azure_openai`, endpoint and deployment in `.env`, signed in
  with `az login` so `DefaultAzureCredential` works on the laptop.
- Request DevOps stages on day one, mirroring AWS: 0 subscription or resource group, model
  deployment, budget alert, developer access; 1 container registry, Container Apps environment,
  identities, Key Vault; 2 Content Safety, tester access; 3 Front Door, static site, job.
- A developer-created resource (e.g. a Content Safety resource) is recorded in PLAN.md with its
  resource ID and handed to DevOps to import into Terraform.

## Seam values to add in `config.py`

```python
llm_provider: Literal["ollama", "bedrock", "azure_openai"] = "ollama"
azure_openai_endpoint: str = ""
azure_openai_deployment: str = ""
azure_openai_api_version: str = ""
guardrail: Literal["noop", "bedrock", "azure_content_safety"] = "noop"
content_safety_endpoint: str = ""
index_store: str = ""   # also accept az://<container>/<prefix>
```

## Open questions for the first Azure project

- Content Safety's groundedness check against Bedrock's grounding score: comparable thresholds?
- Container Apps streaming behaviour and idle timeouts through Front Door.
- Whether AI Foundry Agent Service adds anything over LangGraph on Container Apps.
- Memory store choice and expiry.
