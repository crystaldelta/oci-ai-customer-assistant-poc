"""Tests for publishing an index (AD-22). No network: the index store is a local folder and the
embeddings are fake. The pages are synthetic."""

import shutil

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from assistant import config, publish
from assistant.index_store import LocalStore


def content(tmp_path, pages: int = 3):
    """A content folder with `pages` synthetic pages, copied from the fixtures."""
    folder = tmp_path / "content"
    folder.mkdir(parents=True, exist_ok=True)
    for path in sorted(config.settings.content_dir.glob("*.md"))[:pages]:
        shutil.copy(path, folder / path.name)
    return folder


def run(store, folder, tmp_path, name, **kwargs):
    work = tmp_path / name
    work.mkdir()
    lines: list[str] = []
    code = publish.run(store, folder, work, embeddings=DeterministicFakeEmbedding(size=16), log=lines.append, **kwargs)
    return code, lines


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "store")


def test_the_first_run_publishes_the_content_and_its_index(store, tmp_path):
    folder = content(tmp_path)

    code, _ = run(store, folder, tmp_path, "one")

    meta = store.latest()[0]
    assert code == publish.PUBLISHED
    assert meta["content_id"] == publish.content_id(folder) and meta["pages"] == 3 and meta["points"] > 3
    assert meta["embedding_model"] == config.settings.embedding_model
    assert sorted(p.name for p in (tmp_path / "store" / "content" / meta["content_id"]).glob("*.md")) == sorted(
        p.name for p in folder.glob("*.md")
    )


def test_unchanged_content_is_not_published_again(store, tmp_path):
    folder = content(tmp_path)
    run(store, folder, tmp_path, "one")
    first = store.latest()[0]["index"]

    code, lines = run(store, folder, tmp_path, "two")

    assert code == publish.PUBLISHED and store.latest()[0]["index"] == first
    assert "No change" in lines[-1]


def test_changed_content_gets_a_new_content_id(store, tmp_path):
    folder = content(tmp_path)
    before = publish.content_id(folder)
    page = next(folder.glob("*.md"))
    page.write_text(page.read_text() + "\nOne more line.\n")

    assert publish.content_id(folder) != before


def test_a_much_smaller_index_is_held_back_unless_forced(store, tmp_path):
    run(store, content(tmp_path, pages=6), tmp_path, "one")
    published = store.latest()[0]["index"]
    thin = content(tmp_path / "thin", pages=2)

    code, lines = run(store, thin, tmp_path, "two")

    assert code == publish.NOT_PUBLISHED and store.latest()[0]["index"] == published
    assert "pages fell from 6 to 2" in lines[-1]
    code, _ = run(store, thin, tmp_path, "three", force=True)
    assert code == publish.PUBLISHED and store.latest()[0]["pages"] == 2


def test_another_embedding_model_is_held_back():
    new = {"points": 10, "pages": 2, "embedding_model": "other"}
    old = {"points": 10, "pages": 2, "embedding_model": "BAAI/bge-base-en-v1.5"}

    assert publish.problems(new, old) == ["embedding model other differs from the published BAAI/bge-base-en-v1.5"]


def test_an_empty_content_folder_publishes_nothing(store, tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()

    code, lines = run(store, folder, tmp_path, "one")

    assert code == publish.NOT_PUBLISHED and store.latest() is None
    assert "no Markdown files" in lines[-1]
