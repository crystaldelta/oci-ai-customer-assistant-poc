"""`scripts/pull_prompts.py` and `scripts/push_prompts.py` against a fake Langfuse (AD-11)."""

import json
from types import SimpleNamespace

import pytest
from langfuse.api import NotFoundError

from assistant import config, prompts
from scripts.pull_prompts import pull
from scripts.push_prompts import push


class _Langfuse:
    """Prompt management in memory: name -> list of texts, the last one labelled."""

    def __init__(self, texts=None):
        self.versions = {prompts.LANGFUSE_PREFIX + name: [text] for name, text in (texts or {}).items()}
        self.created = []

    def get_prompt(self, name, **_):
        if name not in self.versions:
            raise NotFoundError(body="not found")
        texts = self.versions[name]
        return SimpleNamespace(prompt=texts[-1], version=len(texts), is_fallback=False)

    def create_prompt(self, *, name, prompt, labels, **_):
        self.created.append((name, labels))
        self.versions.setdefault(name, []).append(prompt)
        return SimpleNamespace(version=len(self.versions[name]))

    def flush(self):
        pass


@pytest.fixture
def repo(tmp_path):
    """A copy of the repo's prompt files."""
    for name in prompts.NAMES:
        (tmp_path / f"{name}.md").write_text(prompts.read_file(config.settings.prompts_dir, name) + "\n")
    return tmp_path


def _all(repo):
    return {name: prompts.read_file(repo, name) for name in prompts.NAMES}


def test_push_seeds_every_prompt_and_records_the_versions(repo):
    client = _Langfuse()

    assert push(client, repo, "production", out=lambda _: None) == 0

    assert len(client.created) == len(prompts.NAMES)
    assert all(labels == ["production"] for _, labels in client.created)
    assert json.loads((repo / prompts.VERSIONS_FILE).read_text()) == dict.fromkeys(prompts.NAMES, 1)


def test_push_never_replaces_an_edit_made_in_langfuse_unless_forced(repo):
    client = _Langfuse({**_all(repo), "system": "EDITED IN LANGFUSE"})
    lines = []

    push(client, repo, "production", out=lines.append)
    assert client.created == []
    assert any(line.startswith("differs    system") for line in lines)

    push(client, repo, "production", force=["system"], out=lines.append)
    assert client.created == [("ai-starter/system", ["production"])]
    assert client.get_prompt("ai-starter/system").version == 2


def test_push_dry_run_changes_nothing(repo):
    client = _Langfuse()

    push(client, repo, "production", dry_run=True, out=lambda _: None)

    assert client.created == []
    assert not (repo / prompts.VERSIONS_FILE).exists()


def test_pull_writes_the_langfuse_versions_into_the_repo(repo):
    client = _Langfuse(_all(repo))
    push(client, repo, "production", out=lambda _: None)  # records version 1 of each
    client.versions["ai-starter/handoff"].append("Complaints: EDITED.")

    assert pull(client, repo, "production", check=True, out=lambda _: None) == 1  # drift
    assert pull(client, repo, "production", out=lambda _: None) == 0

    assert prompts.read_file(repo, "handoff") == "Complaints: EDITED."
    assert json.loads((repo / prompts.VERSIONS_FILE).read_text())["handoff"] == 2
    assert pull(client, repo, "production", check=True, out=lambda _: None) == 0


def test_pull_refuses_a_version_that_fails_the_checks(repo):
    client = _Langfuse({**_all(repo), "emergency": "Leave the area now."})  # lost 000
    lines = []

    assert pull(client, repo, "production", out=lines.append) == 1

    assert prompts.read_file(repo, "emergency") == prompts.read_file(config.settings.prompts_dir, "emergency")
    assert any(line.startswith("refused    emergency") for line in lines)


def test_pull_reports_a_prompt_langfuse_lacks(repo):
    texts = _all(repo)
    del texts["rewrite"]
    lines = []

    assert pull(_Langfuse(texts), repo, "production", out=lines.append) == 1
    assert any(line.startswith("missing    rewrite") for line in lines)
