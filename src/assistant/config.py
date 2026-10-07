"""Typed settings, read from the environment with `.env` as a fallback.

`load_dotenv(override=False)` is deliberate: `.env` holds host-machine URLs
(`localhost:6333`, `localhost:11434`), while Compose sets the container
equivalents (`qdrant:6333`, `host.docker.internal:11434`) in the process
environment. With `override=True` the mounted `.env` would win inside Docker and
every connection would be refused. Loading `.env` into the environment (rather
than giving it to pydantic-settings) keeps that rule in one place.

Each seam (`LLM_PROVIDER`, `RETRIEVER`, `MEMORY`, `GUARDRAIL`, `PROMPT_SOURCE`)
is chosen here; swapping a provider is a settings change, never a code change
in the service (principle 3). Values not built yet are accepted so the choice
is visible, and fail with a clear error until the seam is built.

Read settings as `config.settings.<name>`. Tests change them with
`monkeypatch.setattr(config.settings, ...)`.
"""

from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

load_dotenv(ROOT / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    # ---- Seams
    llm_provider: Literal["ollama", "bedrock", "azure_openai"] = "ollama"
    retriever: Literal["qdrant"] = "qdrant"
    memory: Literal["sqlite", "in_process", "agentcore"] = "sqlite"
    guardrail: Literal["noop", "bedrock"] = "noop"
    prompt_source: Literal["file", "langfuse"] = "file"

    # ---- LLM
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gpt-oss:20b"
    temperature: float = Field(0.1, validation_alias="LLM_TEMPERATURE")
    # Bedrock (Converse API). The model is a setting so it can change without a code change, and
    # has no default: choose one the account has access to. An ID with a `global.` or `us.` prefix
    # is a cross-Region profile, so inference leaves the Region: record that as a decision.
    bedrock_model_id: str = ""
    # Unset means the model's default: some models (OpenAI reasoning models) refuse a temperature.
    bedrock_temperature: float | None = None
    aws_region: str = Field("ap-southeast-2", validation_alias="AWS_REGION")
    # Azure OpenAI: accepted so the choice is visible; the seam is built with the `ai-project-azure`
    # skill on the first Azure project.
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = ""

    # ---- Guardrail. GUARDRAIL=bedrock: the Bedrock Guardrail to apply, in `aws_region`, at a
    # numbered version (never DRAFT, so an edit reaches answers only once tested and published).
    guardrail_id: str = ""
    guardrail_version: str = ""

    # ---- Retrieval. Ingest and query must use the same embedding model: the
    # collection is dimension-locked, so changing it means re-ingesting.
    qdrant_url: str = "http://localhost:6333"
    # An embedded Qdrant index on disk, used instead of the server at `qdrant_url` when set.
    qdrant_path: Path | None = None
    # The published index: `s3://bucket/prefix`, or a local folder with the same layout
    # (`index_store.py`). Set, it replaces `qdrant_path` and `qdrant_url`: the index is downloaded
    # into `index_cache_dir` on the first search, and a newer one is looked for every
    # `index_check_seconds` (twice a day).
    index_store: str = ""
    index_cache_dir: Path = Path("/tmp/assistant-index")
    index_check_seconds: int = 43200
    collection_name: str = Field("content", validation_alias="QDRANT_COLLECTION")
    top_k: int = Field(5, validation_alias="RETRIEVAL_TOP_K")
    # Passages per page among the `top_k`. Pages repeat a question across sections and FAQ pages,
    # so without a cap one page can take most places and push out the page that answers. 0 is no cap.
    max_per_page: int = Field(1, validation_alias="RETRIEVAL_MAX_PER_PAGE")
    # On a conversation's first question (or a rewritten follow-up), the best passage scoring
    # below this means the question is not about the content, and the assistant declines it.
    # Cosine similarity with `embedding_model`. 0.62 was measured on the first project's website
    # content: re-measure on the client's content with `scripts/verify_retrieval.py` (in-scope
    # questions above, off-topic ones below). 0 turns the check off.
    min_score: float = Field(0.62, validation_alias="RETRIEVAL_MIN_SCORE")
    # A passage scoring more than this below the best one is left out of the model's input: it is
    # about something else, and less input means a faster first token. On the first project,
    # relevant passages scored up to 0.10 below the best: re-measure. 0 keeps every passage.
    max_gap: float = Field(0.1, validation_alias="RETRIEVAL_MAX_GAP")
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    embedding_cache_dir: str | None = Field(None, validation_alias="FASTEMBED_CACHE_PATH")
    # What ingest and publish read: Markdown pages with `title` and `url` front matter, or the
    # folder its `LATEST` file names (`data/content`, which `clean.py` writes). The sample in
    # `content/` is synthetic; replace it with the client's. The unit tests pin their own fixtures.
    content_dir: Path = Field(ROOT / "content", validation_alias="CONTENT_DIR")

    # ---- Website content (AD-24, docs/website-content.md): only with the client's written
    # permission. `snapshot.py` fetches raw HTML into `snapshot_dir`; `clean.py` writes Markdown
    # into `site_content_dir`, one folder per snapshot. Both git-ignored.
    # The site to crawl; unset means the origin of `allowed_link_prefix`.
    site_url: str = ""
    # Path prefixes to crawl, comma-separated, in priority order: "/help,/faq,/blog".
    site_sections: str = ""
    # CSS selector for a page's main content; the first that matches on the page is used.
    # `scripts/survey_site.py` suggests one.
    site_content_selector: str = "main, [role=main], article"
    # A header prefix that marks a 403 as the site's own page (Drupal's archived pages carry
    # `x-drupal-`). Unset: every 403 stops the crawl as a refusal.
    site_own_403_header: str = ""
    snapshot_dir: Path = ROOT / "data" / "snapshots"
    site_content_dir: Path = ROOT / "data" / "content"

    # ---- Citations: only a source URL starting with this becomes a link (the client's own site).
    # A source elsewhere keeps its title but loses its URL, so no caller can link it.
    allowed_link_prefix: str = "https://example.invalid/"

    # ---- Memory. The SQLite file keeps conversations across API restarts; it
    # has no expiry, so it is for local use only.
    memory_path: Path = ROOT / "data" / "memory.sqlite"

    # ---- Sources: the pages the answer cites. Off: every page retrieved, for a model that
    # numbers its citations unreliably.
    cited_sources_only: bool = True

    # ---- Safety rules: a message describing a danger gets the fixed `emergency` reply before any
    # search or model call (`rules.py`). Off for a project with no safety-critical situations, a
    # decision recorded with the client; `new_project.py --safety-rules` sets it.
    safety_rules: bool = True

    # ---- Follow-ups: a turn with earlier turns is searched as a standalone question, written by
    # a model call before the search. Off: the message is searched as it is.
    rewrite_follow_ups: bool = True

    # ---- Prompts: the repo files, or Langfuse with the files as fallback.
    prompts_dir: Path = Path(__file__).resolve().parent / "prompts"
    prompt_cache_seconds: int = 60
    # The Langfuse label served: `production`, or `staging` to try an edit on a local stack.
    prompt_label: str = "production"
    # A short wait for Langfuse, and after a failure the files for this long before trying again,
    # so an outage costs one wait a minute, not one a turn.
    prompt_timeout_seconds: int = 2
    prompt_retry_seconds: int = 60

    # ---- Tracing: on only when both keys are set.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"

    # ---- Thread IDs (see threads.py). Unset locally means a random
    # per-process secret; any shared deployment must set it (32+ characters).
    thread_id_secret: str = ""

    # ---- Where the API runs a turn: the service in this process, or the AgentCore entry point
    # through `/invocations`, by the runtime's ARN (on AWS) or by URL (the local container).
    agent_runtime: Literal["in_process", "agentcore"] = "in_process"
    agentcore_runtime_arn: str = ""
    agentcore_qualifier: str = "DEFAULT"
    agentcore_url: str = ""

    # ---- Abuse limits for the anonymous endpoints. Every request costs tokens.
    max_question_chars: int = 2000
    max_turns_per_thread: int = 30
    rate_limit_per_minute: int = 20
    # The request header that holds the caller's address, set by a proxy the caller cannot get
    # around (CloudFront's `CloudFront-Viewer-Address`). Unset: the socket peer address.
    client_address_header: str = ""

    def tracing_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


settings = Settings()
