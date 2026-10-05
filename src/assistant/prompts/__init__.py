"""The `prompts` seam: the text the agent is instructed with, chosen by
`PROMPT_SOURCE` (AD-11).

Prompts are edited in Langfuse prompt management. With `PROMPT_SOURCE=langfuse`
the service asks Langfuse for the `PROMPT_LABEL` version (`production`), cached
for `PROMPT_CACHE_SECONDS`, and uses the file in this directory whenever
Langfuse cannot answer or a fetched prompt fails `problem()`, so answering
never depends on Langfuse (principle 8). Without the Langfuse keys, or with
`PROMPT_SOURCE=file`, the files are used as they are.

The files are the fallback and the reviewed record of what is live:
`scripts/pull_prompts.py` copies the Langfuse versions here at each release and
records their numbers in `versions.json`, and `scripts/push_prompts.py` seeds
Langfuse with any prompt it lacks (see `docs/prompt-management.md`).

The system prompt follows the client's tone and style guide, once there is one.
`emergency` and `decline` are fixed replies that `route` (or the score floor) gives without a
model call; `handoff` is added to the system message for the turns it applies to. `rewrite`
instructs the model call that turns a follow-up into a standalone search.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from assistant.config import Settings

log = logging.getLogger("ai-starter")

# Prompt files, by name. The Langfuse name is `LANGFUSE_PREFIX + name`.
NAMES = ("system", "emergency", "decline", "handoff", "rewrite")
LANGFUSE_PREFIX = "ai-starter/"
VERSIONS_FILE = "versions.json"

# Text a prompt must keep, because code depends on it or safety does: the word the rewrite
# answers with when there is nothing to search, and the emergency number in the safety reply
# (AD-12). Add "[1]" to `emergency` when `rules.EMERGENCY_PAGE` gives the reply a page to cite,
# and the local number if the assistant serves outside Australia. A placeholder the service
# fills (`{region}`, say) goes here too.
REQUIRED = {
    "rewrite": ("NONE",),
    "emergency": ("000",),
}


def problem(name: str, text: str) -> str | None:
    """Why a prompt's text cannot be used, or None when it can."""
    if not text.strip():
        return "it is empty"
    missing = [part for part in REQUIRED.get(name, ()) if part not in text]
    return f"it lacks {', '.join(missing)}" if missing else None


def read_file(prompts_dir: Path, name: str) -> str:
    return (prompts_dir / f"{name}.md").read_text(encoding="utf-8").strip()


def read_versions(prompts_dir: Path) -> dict[str, int]:
    """The Langfuse version each file was pulled from; empty before the first pull."""
    try:
        return json.loads((prompts_dir / VERSIONS_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def write_versions(prompts_dir: Path, versions: dict[str, int]) -> None:
    ordered = {name: versions[name] for name in NAMES if name in versions}
    (prompts_dir / VERSIONS_FILE).write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    version: int | None = None  # the Langfuse version; None when the file is served
    ref: Any = None  # the Langfuse prompt, which links a model call to it in a trace

    @property
    def served(self) -> int | str:
        """What a trace records for it: the Langfuse version, or "file"."""
        return "file" if self.version is None else self.version


class PromptSource(Protocol):
    def get_prompt(self, name: str) -> Prompt: ...

    def get(self, name: str) -> str: ...

    def status(self) -> dict: ...


class FilePrompts:
    def __init__(self, prompts_dir: Path) -> None:
        # Read once: a prompt change needs a restart, like a code change.
        self._prompts = {name: Prompt(name, read_file(prompts_dir, name)) for name in NAMES}
        self.versions = read_versions(prompts_dir)

    def get_prompt(self, name: str) -> Prompt:
        return self._prompts[name]

    def get(self, name: str) -> str:
        return self._prompts[name].text

    def status(self) -> dict:
        return {"source": "file"}


class LangfusePrompts:
    """Langfuse prompt management with the repo file as fallback.

    The SDK serves from its cache and refreshes expired entries in the
    background, so once a prompt has been fetched an outage costs nothing.
    Before that, a fetch waits up to `timeout_seconds`; after a failure every
    prompt comes from the files for `retry_seconds`, so an outage at start-up
    costs one wait, not one a turn. A prompt Langfuse lacks is a problem with
    that prompt alone, and is not tried again for `retry_seconds` either.
    """

    def __init__(
        self,
        client,
        fallback: FilePrompts,
        *,
        label: str = "production",
        cache_seconds: int = 60,
        timeout_seconds: int = 2,
        retry_seconds: int = 60,
        clock=time.monotonic,
    ) -> None:
        self._client = client
        self._fallback = fallback
        self._label = label
        self._cache_seconds = cache_seconds
        self._timeout_seconds = timeout_seconds
        self._retry_seconds = retry_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._down_until = 0.0  # Langfuse failed: the files until then
        self._missing_until: dict[str, float] = {}  # Langfuse lacks the prompt: the file until then
        self._from_file: set[str] = set()  # served from the file on its latest request
        self._stale: dict[str, int] = {}  # Langfuse version, where it differs from versions.json
        self._rejected: set[tuple[str, int]] = set()  # logged once per version

    def get_prompt(self, name: str) -> Prompt:
        now = self._clock()
        with self._lock:
            skip = now < self._down_until or now < self._missing_until.get(name, 0.0)
        if skip:
            return self._file(name)
        try:
            fetched = self._client.get_prompt(
                LANGFUSE_PREFIX + name,
                label=self._label,
                cache_ttl_seconds=self._cache_seconds,
                max_retries=0,
                fetch_timeout_seconds=self._timeout_seconds,
            )
        except Exception as error:  # noqa: BLE001 — never let prompt management stop an answer
            self._failed(name, error, now)
            return self._file(name)
        if getattr(fetched, "is_fallback", False):  # the SDK's own fallback: not from Langfuse
            self._failed(name, RuntimeError("the SDK returned its fallback"), now)
            return self._file(name)

        text, version = fetched.prompt.strip(), fetched.version
        reason = problem(name, text)
        if reason:
            with self._lock:
                first = (name, version) not in self._rejected
                self._rejected.add((name, version))
            if first:
                log.warning("Langfuse prompt %r version %s is not used: %s; using the repo file", name, version, reason)
            return self._file(name)
        self._check_stale(name, version)
        with self._lock:
            self._from_file.discard(name)
        return Prompt(name, text, version, fetched)

    def get(self, name: str) -> str:
        return self.get_prompt(name).text

    def status(self) -> dict:
        with self._lock:
            return {
                "source": "langfuse",
                "label": self._label,
                "fallback": sorted(self._from_file),
                "stale": sorted(self._stale),
            }

    def warm(self) -> None:
        """Fetch every prompt once, so the first turns read them from the cache."""
        for name in NAMES:
            self.get_prompt(name)

    def _file(self, name: str) -> Prompt:
        with self._lock:
            self._from_file.add(name)
        return self._fallback.get_prompt(name)

    def _failed(self, name: str, error: Exception, now: float) -> None:
        from langfuse.api import NotFoundError

        with self._lock:
            if isinstance(error, NotFoundError):
                self._missing_until[name] = now + self._retry_seconds
            else:
                self._down_until = now + self._retry_seconds
        if isinstance(error, NotFoundError):
            log.warning("Langfuse has no prompt %r labelled %r; using the repo file", name, self._label)
        else:
            log.warning(
                "Langfuse prompts unavailable (%s); using the repo files for %ss",
                error,
                self._retry_seconds,
                exc_info=True,
            )

    def _check_stale(self, name: str, version: int) -> None:
        pulled = self._fallback.versions.get(name)
        with self._lock:
            known = self._stale.get(name)
            if pulled == version:
                self._stale.pop(name, None)
                return
            self._stale[name] = version
        if known != version:
            log.warning(
                "prompt %r: Langfuse v%s, the image's fallback is v%s; run scripts/pull_prompts.py at the next release",
                name,
                version,
                pulled if pulled is not None else "unknown",
            )


def get_prompt_source(settings: Settings) -> PromptSource:
    files = FilePrompts(settings.prompts_dir)
    if settings.prompt_source == "file":
        return files
    if not settings.tracing_enabled():
        log.warning("PROMPT_SOURCE=langfuse needs LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY; using the repo files")
        return files
    from assistant import tracing

    source = LangfusePrompts(
        tracing.client(settings),
        files,
        label=settings.prompt_label,
        cache_seconds=settings.prompt_cache_seconds,
        timeout_seconds=settings.prompt_timeout_seconds,
        retry_seconds=settings.prompt_retry_seconds,
    )
    # In the background: building the components must make no network call.
    threading.Thread(target=source.warm, name="prompt-warm-up", daemon=True).start()
    return source
