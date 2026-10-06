"""Tests for the scheduled website refresh. No network: a mock transport plays a synthetic site,
the index store is a local folder and the embeddings are fake."""

import hashlib
import json

import httpx
import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from assistant import config, refresh, snapshot
from assistant.index_store import LocalStore

SITE = "https://www.example.invalid"
PATHS = ["/help/pay", "/help/move", "/help/solar"]
LASTMOD = "2026-08-01T09:00:00+10:00"


@pytest.fixture(autouse=True)
def the_site(monkeypatch):
    monkeypatch.setattr(config.settings, "site_url", SITE)
    monkeypatch.setattr(config.settings, "site_sections", "/help")


def html(title: str, text: str) -> str:
    return (
        f"<html><head><title>{title} | Example Co</title></head>"
        f"<body><main><h1>{title}</h1><p>{text}</p></main></body></html>"
    )


PAGES = {path: html(path.rsplit("/", 1)[1].title(), f"Synthetic test text about {path}.") for path in PATHS}


def sitemap(lastmods: dict[str, str]) -> str:
    urls = "".join(f"<url><loc>{SITE}{p}</loc><lastmod>{m}</lastmod></url>" for p, m in lastmods.items())
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'


def seed_snapshot(tmp_path):
    """A snapshot as a full crawl would leave it."""
    folder = tmp_path / "seed"
    (folder / "pages").mkdir(parents=True)
    pages = {SITE + p: LASTMOD for p in PATHS}
    (folder / "sitemap.json").write_text(json.dumps({"sitemaps": [f"{SITE}/sitemap.xml"], "pages": pages}))
    entries = []
    for path, text in PAGES.items():
        name = snapshot.file_name(SITE + path)
        (folder / "pages" / name).write_text(text)
        entries.append(
            {
                "url": SITE + path,
                "fetched_at": "2026-09-30T00:00:00+00:00",
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "file": f"pages/{name}",
                "lastmod": LASTMOD,
            }
        )
    manifest = {"sections": ["/help"], "pages": entries, "snapshot_id": snapshot.snapshot_id(entries)}
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return folder


def mock_site(lastmods: dict[str, str] | None = None, pages: dict[str, str] | None = None, status: dict | None = None):
    requests: list[str] = []
    lastmods = dict.fromkeys(PATHS, LASTMOD) if lastmods is None else lastmods
    pages = {**PAGES, **(pages or {})}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append(path)
        if status and path in status:
            return httpx.Response(status[path], text="Access Denied")
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap(lastmods))
        if path in pages:
            return httpx.Response(200, text=pages[path], headers={"content-type": "text/html"})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler)), requests


def job(store, tmp_path, name, client=None, **kwargs):
    work = tmp_path / name
    work.mkdir()
    return refresh.run(
        store,
        work,
        client=client,
        sleep=lambda _: None,
        embeddings=DeterministicFakeEmbedding(size=16),
        log=lambda _: None,
        **kwargs,
    )


@pytest.fixture
def seeded(tmp_path):
    store = LocalStore(tmp_path / "store")

    def no_requests(request):
        raise AssertionError(f"seeding made a request: {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(no_requests))
    assert job(store, tmp_path, "seed-run", client=client, seed=seed_snapshot(tmp_path)) == refresh.PUBLISHED
    return store


def test_seeding_publishes_an_index_and_its_snapshot_without_asking_the_site(seeded):
    meta, _ = seeded.latest()

    assert meta["pages"] == 3 and meta["points"] >= 3
    assert (seeded.root / "snapshots" / meta["snapshot_id"] / "manifest.json").exists()
    assert len(list((seeded.root / "content" / meta["content_id"]).glob("*.md"))) == 3


def test_an_unchanged_site_costs_two_requests_and_publishes_nothing(seeded, tmp_path):
    before = seeded.etag()
    client, requests = mock_site()

    assert job(seeded, tmp_path, "run", client=client) == refresh.PUBLISHED

    assert requests == ["/robots.txt", "/sitemap.xml"]
    assert seeded.etag() == before


def test_a_changed_page_is_fetched_and_a_new_index_published(seeded, tmp_path):
    old = seeded.latest()[0]
    lastmods = dict.fromkeys(PATHS, LASTMOD) | {PATHS[0]: "2026-09-30T09:00:00+10:00"}
    client, requests = mock_site(lastmods, pages={PATHS[0]: html("Pay", "Synthetic: pay by card.")})

    assert job(seeded, tmp_path, "run", client=client) == refresh.PUBLISHED

    new = seeded.latest()[0]
    assert requests == ["/robots.txt", "/sitemap.xml", PATHS[0]]
    assert new["snapshot_id"] != old["snapshot_id"] and new["index"] != old["index"]


def test_a_thinner_site_is_held_back_for_a_person(seeded, tmp_path):
    before = seeded.etag()
    client, _ = mock_site({PATHS[0]: LASTMOD})  # two of three pages left the sitemap

    assert job(seeded, tmp_path, "run", client=client) == refresh.NOT_PUBLISHED
    assert seeded.etag() == before

    client, _ = mock_site({PATHS[0]: LASTMOD})
    assert job(seeded, tmp_path, "forced", client=client, force=True) == refresh.PUBLISHED
    assert seeded.latest()[0]["pages"] == 1


def test_a_refusal_publishes_nothing_and_keeps_the_response_for_review(seeded, tmp_path):
    before = seeded.etag()
    client, requests = mock_site(status={"/sitemap.xml": 429})

    assert job(seeded, tmp_path, "run", client=client) == refresh.REFUSED

    assert requests == ["/robots.txt", "/sitemap.xml"]  # stopped, no retry
    assert seeded.etag() == before
    [record] = (seeded.root / "refused").iterdir()
    assert "HTTP 429" in record.read_text()


def test_a_first_run_can_crawl_the_sections(tmp_path):
    store = LocalStore(tmp_path / "store")
    client, requests = mock_site()

    assert job(store, tmp_path, "crawl", client=client, crawl=True) == refresh.PUBLISHED

    meta = store.latest()[0]
    assert meta["pages"] == len(PATHS)
    assert set(PATHS) <= set(requests)
    # The crawl is the baseline: the next run refreshes from it with two requests.
    client, requests = mock_site()
    assert job(store, tmp_path, "run", client=client) == refresh.PUBLISHED
    assert requests == ["/robots.txt", "/sitemap.xml"]


def test_a_refused_crawl_publishes_nothing(tmp_path):
    store = LocalStore(tmp_path / "store")
    client, _ = mock_site(status={PATHS[1]: 429})

    assert job(store, tmp_path, "crawl", client=client, crawl=True) == refresh.REFUSED
    assert store.latest() is None
    assert len(list((store.root / "refused").iterdir())) == 1


def test_nothing_published_yet_means_seed_or_crawl_first(tmp_path):
    client, requests = mock_site()

    assert job(LocalStore(tmp_path / "empty"), tmp_path, "run", client=client) == refresh.NOT_PUBLISHED
    assert requests == []


def test_an_index_published_from_markdown_has_no_snapshot_to_refresh(tmp_path):
    store = LocalStore(tmp_path / "store")
    store._write("LATEST.json", json.dumps({"index": "x", "content_id": "y"}).encode())  # from publish.py
    client, requests = mock_site()

    assert job(store, tmp_path, "run", client=client) == refresh.NOT_PUBLISHED
    assert requests == []
