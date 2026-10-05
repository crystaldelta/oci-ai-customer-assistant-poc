"""Test settings, applied before any `assistant` module is imported.

Settings are read once at import, and `.env` fills in anything unset, so a
developer's `.env` (tracing keys, `PROMPT_SOURCE=langfuse`, a SQLite memory
file) would otherwise leak into the tests. Pinning the values here means the
suite never touches a real provider, file or network.
"""

import os
from pathlib import Path

os.environ.update(
    {
        # The synthetic test corpus, never the content the stack serves.
        "CONTENT_DIR": str(Path(__file__).parent / "fixtures" / "content"),
        "LLM_PROVIDER": "ollama",  # constructed, never called
        "RETRIEVER": "qdrant",  # constructed, never searched
        "MEMORY": "in_process",
        "GUARDRAIL": "noop",
        "SAFETY_RULES": "true",  # the mechanism is tested even in a project that turns it off
        "PROMPT_SOURCE": "file",
        "LANGFUSE_PUBLIC_KEY": "",
        "LANGFUSE_SECRET_KEY": "",
        "THREAD_ID_SECRET": "",
        "INDEX_STORE": "",  # never a real bucket
        "ALLOWED_LINK_PREFIX": "https://example.invalid/",
        # As in assistant/__init__.py: onnxruntime's telemetry thread can abort Python at exit.
        "ORT_DISABLE_TELEMETRY": "1",
    }
)

import pytest  # noqa: E402 — after the environment is pinned


@pytest.fixture
def fakes():
    """Run the service on in-memory fakes for every seam; see fakes.py."""
    from fakes import fake_components

    from assistant import service as svc

    components = fake_components()
    svc.use(components)
    yield components
    svc.use(None)
