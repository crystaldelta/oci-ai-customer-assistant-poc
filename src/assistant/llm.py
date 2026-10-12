"""The `llm` seam: the chat model the agent runs on, chosen by `LLM_PROVIDER`.

The model must support streaming; it answers once per turn (plus one call to
rewrite a follow-up) and needs no tools, because the graph retrieves before it
runs (AD-03).

- `ollama` (local default): Ollama on the host.
- `bedrock`: any Bedrock model that serves the Converse API, named by
  `BEDROCK_MODEL_ID` (a model ID or an inference profile ID), in `AWS_REGION`.
  Credentials come from the usual AWS chain: the runtime's role on AgentCore,
  a profile or environment variables on a developer machine.
- `azure_openai`: accepted, not built yet. Build it with the `ai-project-azure`
  skill (`langchain-openai`'s `AzureChatOpenAI` with a managed identity).
"""

import logging

from langchain_core.language_models import BaseChatModel

from assistant.config import Settings

log = logging.getLogger("ai-starter")


def get_chat_model(settings: Settings) -> BaseChatModel:
    """Build the chat model. Construction makes no network call."""
    if settings.llm_provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            temperature=settings.temperature,
            reasoning=False,
        )
    if settings.llm_provider == "bedrock":
        if not settings.bedrock_model_id:
            raise ValueError("LLM_PROVIDER=bedrock needs BEDROCK_MODEL_ID")
        from langchain_aws import ChatBedrockConverse

        return ChatBedrockConverse(
            model=settings.bedrock_model_id,
            region_name=settings.aws_region,
            temperature=settings.bedrock_temperature,
        )
    if settings.llm_provider == "azure_openai":
        raise NotImplementedError(
            "LLM_PROVIDER=azure_openai is not built yet: add it here with the ai-project-azure skill "
            "(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT are its settings)"
        )
    raise NotImplementedError(f"LLM_PROVIDER={settings.llm_provider} is not supported")


def model_name(settings: Settings) -> str:
    """The model ID reported to callers and traces."""
    if settings.llm_provider == "ollama":
        return settings.ollama_model
    if settings.llm_provider == "azure_openai":
        return settings.azure_openai_deployment
    return settings.bedrock_model_id


def health(settings: Settings) -> dict:
    """Reachability of the model provider. Never raises.

    A cloud model is not called: a health check that ran a model would cost tokens on every probe.
    """
    status = {"llm_provider": settings.llm_provider, "llm_model": model_name(settings)}
    if settings.llm_provider == "bedrock":
        return status | {"aws_region": settings.aws_region}
    if settings.llm_provider != "ollama":
        return status
    import httpx

    status |= {"ollama_url": settings.ollama_base_url, "ollama_ok": False}
    try:
        httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=3).raise_for_status()
        status["ollama_ok"] = True
    except Exception:  # noqa: BLE001 — health must not raise
        # The detail goes to the log, not the response: /api/health is public.
        log.warning("health: ollama unreachable", exc_info=True)
        status["error"] = "ollama: unreachable"
    return status
