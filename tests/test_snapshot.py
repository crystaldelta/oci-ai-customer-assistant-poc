"""Tests for the website snapshot fetcher. No network: a mock transport plays a synthetic site."""

import gzip
import json
import random

import httpx
import pytest

from assistant import config, snapshot
from assistant.snapshot import Blocked, Fetcher, canonical, crawl

SITE = "https://www.example.invalid"
SECTIONS = ["/help"]

ROBOTS = "User-agent: *\nDisallow: /help/private\n"
SITEMAP = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{SITE}/help/</loc><lastmod>2026-09-01T10:00:00+10:00</lastmod></url>
  <url><loc>{SITE}/help/pay</loc><lastmod>2026-08-15T09:00:00+10:00</lastmod></url>
  <url><loc>{SITE}/help/unlinked</loc><lastmod>2026-07-01T09:00:00+10:00</lastmod></url>
  <url><loc>{SITE}/news/item</loc><lastmod>2026-07-01T09:00:00+10:00</lastmod></url>
</urlset>"""


def page(*hrefs: str) -> str:
    return "<html><body>" + "".join(f'<a href="{h}">link</a>' for h in hrefs) + "</body></html>"


PAGES = {
    "/help": page(
        "/help/pay",
        "/help/pay/#top",  # same page again
        "pay?utm=x",  # relative, with a query
        "/help/private",  # disallowed by robots.txt
        "/help/guide.pdf",  # a file, not a page
        "/plans",  # outside the sections
        "https://elsewhere.example/",  # off the site
        "/help/old",  # redirects
        "/help/missing",
    ),
    "/help/pay": page("/help/pay/card"),
    "/help/pay/card": page("/help/pay/card/visa"),  # depth 3: not followed
    "/help/new": page(),
    "/help/unlinked": page(),  # only in the sitemap
}


def xml(text: str):
    return lambda _: httpx.Response(200, text=text, headers={"content-type": "application/xml"})


def html(text: str):
    return lambda _: httpx.Response(200, text=text, headers={"content-type": "text/html; charset=utf-8"})


def site(overrides: dict | None = None):
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        path = request.url.path.rstrip("/") or "/"
        if overrides and path in overrides:
            return overrides[path](request)
        if path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        if path == "/sitemap.xml":
            return xml(SITEMAP)(request)
        if path == "/help/old":
            return httpx.Response(301, headers={"location": f"{SITE}/help/new"})
        if path in PAGES:
            return html(PAGES[path])(request)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": snapshot.USER_AGENT})
    return client, requests


def run(tmp_path, overrides=None, own_403_header="", **kwargs):
    client, requests = site(overrides)
    waits: list[float] = []
    fetcher = Fetcher(client, sleep=waits.append, rng=random.Random(1), own_403_header=own_403_header)
    manifest = crawl(fetcher, tmp_path, site=SITE, log=lambda _: None, **{"sections": SECTIONS, **kwargs})
    return manifest, requests, waits


def test_crawl_follows_links_within_the_sections_and_saves_each_page_once(tmp_path):
    manifest, _, _ = run(tmp_path)

    saved = [p["url"] for p in manifest["pages"]]
    assert saved == [
        f"{SITE}/help",
        f"{SITE}/help/pay",
        f"{SITE}/help/unlinked",  # queued from the sitemap
        f"{SITE}/help/new",
        f"{SITE}/help/pay/card",
    ]
    reasons = {e["url"].removeprefix(SITE): e["reason"] for e in manifest["not_saved"]}
    assert reasons["/help/private"] == "disallowed by robots.txt"
    assert reasons["/help/missing"] == "HTTP 404"
    assert "/help/guide.pdf" not in reasons  # never requested
    assert "/plans" not in reasons
    assert "/news/item" not in reasons  # in the sitemap, outside the sections


def test_redirected_pages_are_saved_under_their_final_url(tmp_path):
    manifest, _, _ = run(tmp_path)

    entry = next(p for p in manifest["pages"] if p["url"] == f"{SITE}/help/new")
    assert entry["requested_url"] == f"{SITE}/help/old"


def test_earlier_sections_come_first_at_each_depth(tmp_path):
    # Every page links to every section in its navigation, before its own content.
    with_nav = html(page("/bills/due", "/help/pay"))

    manifest, _, _ = run(
        tmp_path, overrides={"/help": with_nav, "/bills": with_nav}, sections=["/help", "/bills"], max_pages=3
    )

    assert [p["url"].removeprefix(SITE) for p in manifest["pages"]] == ["/help", "/bills", "/help/pay"]


def test_the_section_slash_crawls_the_whole_site(tmp_path):
    overrides = {"/": html(page("/help", "/plans")), "/plans": html(page())}

    manifest, _, _ = run(tmp_path, overrides=overrides, sections=["/"], max_depth=1)

    urls = {p["url"].removeprefix(SITE) for p in manifest["pages"]}
    assert {"/", "/help", "/plans"} <= urls  # /plans is outside every section but "/"
    assert manifest["pages"][0]["file"] == "pages/index.html"


def test_links_beyond_the_depth_limit_are_not_fetched(tmp_path):
    _, requests, _ = run(tmp_path)

    assert "/help/pay/card/visa" not in requests


def test_every_request_after_the_first_waits_a_random_interval_in_range(tmp_path):
    manifest, requests, waits = run(tmp_path)

    assert len(waits) == len(requests) - 1 == manifest["requests"] - 1
    assert all(snapshot.MIN_DELAY <= w <= snapshot.MAX_DELAY for w in waits)
    assert len(set(waits)) > 1  # random, not fixed


def test_a_longer_crawl_delay_in_robots_txt_raises_the_wait(tmp_path):
    robots = lambda _: httpx.Response(200, text="User-agent: *\nCrawl-delay: 3\n")  # noqa: E731
    _, _, waits = run(tmp_path, overrides={"/robots.txt": robots})

    assert waits and all(w == 3 for w in waits[1:])


def test_the_manifest_records_hashes_files_and_lastmod(tmp_path):
    manifest, _, _ = run(tmp_path)

    on_disk = json.loads((tmp_path / "manifest.json").read_text())
    assert on_disk["snapshot_id"] == manifest["snapshot_id"]
    assert on_disk["user_agent"] == snapshot.USER_AGENT
    hub, pay = on_disk["pages"][:2]
    assert hub["file"] == "pages/help.html"
    assert (tmp_path / hub["file"]).read_text() == PAGES["/help"]
    assert hub["lastmod"] == "2026-09-01T10:00:00+10:00"
    assert pay["lastmod"] == "2026-08-15T09:00:00+10:00"
    sitemap = json.loads((tmp_path / "sitemap.json").read_text())
    assert sitemap["sitemaps"] == [f"{SITE}/sitemap.xml"]
    assert len(sitemap["pages"]) == 4


def test_sitemaps_named_in_robots_txt_and_sitemap_indexes_are_followed(tmp_path):
    robots = lambda _: httpx.Response(200, text=f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap_index.xml\n")  # noqa: E731
    index = f"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>{SITE}/pages.xml.gz</loc></sitemap><sitemap><loc>{SITE}/broken.xml</loc></sitemap>
    </sitemapindex>"""
    child = f"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>{SITE}/help/unlinked</loc></url></urlset>"""
    gz = lambda _: httpx.Response(200, content=gzip.compress(child.encode()))  # noqa: E731
    overrides = {"/robots.txt": robots, "/sitemap_index.xml": xml(index), "/pages.xml.gz": gz, "/broken.xml": xml("<")}

    manifest, requests, _ = run(tmp_path, overrides=overrides)

    assert "/sitemap.xml" not in requests
    saved = json.loads((tmp_path / "sitemap.json").read_text())
    assert saved["sitemaps"] == [f"{SITE}/sitemap_index.xml", f"{SITE}/pages.xml.gz"]
    assert saved["pages"] == {f"{SITE}/help/unlinked": None}
    entry = next(p for p in manifest["pages"] if p["url"] == f"{SITE}/help/unlinked")
    assert entry["lastmod"] is None


def test_the_snapshot_id_depends_only_on_the_content(tmp_path):
    first, _, _ = run(tmp_path / "a")
    second, _, _ = run(tmp_path / "b")
    third, _, _ = run(tmp_path / "c", overrides={"/help/new": html(page("/help"))})

    assert first["snapshot_id"] == second["snapshot_id"] != third["snapshot_id"]


def test_max_pages_stops_the_crawl(tmp_path):
    manifest, _, _ = run(tmp_path, max_pages=2)

    assert len(manifest["pages"]) == 2
    assert manifest["unvisited"] > 0


@pytest.mark.parametrize("status", [403, 429])
def test_being_refused_stops_the_crawl_and_keeps_the_partial_manifest(tmp_path, status):
    def refused(_):
        return httpx.Response(status, text="Access Denied. Reference #18.abc", headers={"server": "AkamaiGHost"})

    with pytest.raises(Blocked):
        run(tmp_path, overrides={"/help/pay": refused})

    partial = json.loads((tmp_path / "manifest.json").read_text())
    assert [p["url"] for p in partial["pages"]] == [f"{SITE}/help"]
    assert "snapshot_id" not in partial
    assert partial["refused"] == {"url": f"{SITE}/help/pay", "status": status, "file": "refused.txt"}
    record = (tmp_path / "refused.txt").read_text()
    assert f"HTTP {status}" in record
    assert "server: AkamaiGHost" in record
    assert record.endswith("Access Denied. Reference #18.abc")


def test_a_403_page_from_the_site_is_skipped_only_when_its_header_is_named(tmp_path):
    archived = lambda _: httpx.Response(  # noqa: E731
        403, text="<title>Page not found</title>", headers={"x-drupal-dynamic-cache": "HIT"}
    )

    with pytest.raises(Blocked):  # by default every 403 is a refusal
        run(tmp_path / "default", overrides={"/help/pay": archived})

    manifest, _, _ = run(tmp_path / "named", overrides={"/help/pay": archived}, own_403_header="x-drupal-")

    reasons = {e["url"].removeprefix(SITE): e["reason"] for e in manifest["not_saved"]}
    assert reasons["/help/pay"] == "HTTP 403 (site page)"
    assert f"{SITE}/help/new" in [p["url"] for p in manifest["pages"]]  # the crawl went on
    assert "Page not found" in (tmp_path / "named" / "refused" / "help__pay.txt").read_text()


def test_resume_continues_where_the_crawl_stopped_without_refetching(tmp_path):
    full, _, _ = run(tmp_path / "full")
    blocked = lambda _: httpx.Response(403, text="Access Denied")  # noqa: E731
    with pytest.raises(Blocked):
        run(tmp_path / "partial", overrides={"/help/new": blocked})
    before = json.loads((tmp_path / "partial" / "manifest.json").read_text())

    resumed, requests, _ = run(tmp_path / "partial", resume=True)

    assert [p["url"] for p in resumed["pages"]] == [p["url"] for p in full["pages"]]
    assert resumed["snapshot_id"] == full["snapshot_id"]
    already = {p["url"].removeprefix(SITE) for p in before["pages"]} | {"/sitemap.xml"}
    assert already.isdisjoint(requests)  # saved pages are read from disk
    assert "/help/old" in requests  # the refused URL (after its redirect) is tried again
    assert resumed["requests"] == before["requests"] + len(requests)
    assert "refused" not in resumed
    assert not (tmp_path / "partial" / "refused.txt").exists()


def test_canonical_drops_query_fragment_and_trailing_slash_and_rejects_other_hosts():
    assert canonical(f"{SITE}/help/?a=1#x", SITE) == f"{SITE}/help"
    assert canonical("http://WWW.EXAMPLE.INVALID/", SITE) == f"{SITE}/"
    assert canonical("https://elsewhere.example/help", SITE) is None
    assert canonical("mailto:help@example.invalid", SITE) is None


def test_the_site_and_sections_come_from_the_settings(monkeypatch):
    with pytest.raises(SystemExit):  # the starter's placeholder prefix is not a site
        snapshot.site_url()
    monkeypatch.setattr(config.settings, "allowed_link_prefix", "https://www.client.example/help/")
    assert snapshot.site_url() == "https://www.client.example"
    monkeypatch.setattr(config.settings, "site_url", "https://WWW.Other.example/")
    assert snapshot.site_url() == "https://www.other.example"

    assert snapshot.sections() == ["/"]
    monkeypatch.setattr(config.settings, "site_sections", " /help/, faq ,/blog")
    assert snapshot.sections() == ["/help", "/faq", "/blog"]


# ------------------------------------------------------------------ refresh
#
# A refresh starts from a full crawl of the mock site: /help, /pay and /unlinked are in the
# sitemap with a lastmod; /new (reached through a redirect) and /pay/card are not.


def refreshed(tmp_path, sitemap: str = SITEMAP, overrides: dict | None = None, before: str = SITEMAP):
    previous, _, _ = run(tmp_path / "prev", overrides={"/sitemap.xml": xml(before)})
    client, requests = site({"/sitemap.xml": xml(sitemap), **(overrides or {})})
    waits: list[float] = []
    fetcher = Fetcher(client, sleep=waits.append, rng=random.Random(1))
    manifest = snapshot.refresh(
        fetcher, tmp_path / "prev", tmp_path / "new", site=SITE, sections=SECTIONS, log=lambda _: None
    )
    return manifest, previous, requests, waits


def test_a_refresh_of_an_unchanged_site_makes_two_requests_and_keeps_the_snapshot_id(tmp_path):
    manifest, previous, requests, _ = refreshed(tmp_path)

    assert requests == ["/robots.txt", "/sitemap.xml"]
    assert manifest["snapshot_id"] == previous["snapshot_id"]
    assert manifest["carried"] == len(previous["pages"]) and manifest["fetched"] == 0
    assert manifest["mode"] == "refresh" and manifest["previous"] == previous["snapshot_id"]
    for entry in manifest["pages"]:
        assert (tmp_path / "new" / entry["file"]).read_text() == (tmp_path / "prev" / entry["file"]).read_text()


def test_a_refresh_fetches_only_the_page_whose_lastmod_changed(tmp_path):
    sitemap = SITEMAP.replace("2026-08-15T09:00:00+10:00", "2026-09-20T09:00:00+10:00")

    manifest, previous, requests, waits = refreshed(tmp_path, sitemap, {"/help/pay": html(page("/help"))})

    assert requests == ["/robots.txt", "/sitemap.xml", "/help/pay"]
    assert len(waits) == 2 and all(snapshot.MIN_DELAY <= w <= snapshot.MAX_DELAY for w in waits)
    assert manifest["fetched"] == 1
    assert manifest["snapshot_id"] != previous["snapshot_id"]
    pay = next(p for p in manifest["pages"] if p["url"] == f"{SITE}/help/pay")
    assert pay["lastmod"] == "2026-09-20T09:00:00+10:00"
    assert (tmp_path / "new" / pay["file"]).read_text() == page("/help")


def test_a_refresh_fetches_a_page_new_to_the_sitemap_without_following_its_links(tmp_path):
    entry = f"<url><loc>{SITE}/help/extra</loc><lastmod>2026-09-25T09:00:00+10:00</lastmod></url>"

    manifest, _, requests, _ = refreshed(
        tmp_path, SITEMAP.replace("</urlset>", entry + "</urlset>"), {"/help/extra": html(page("/help/deeper"))}
    )

    assert requests == ["/robots.txt", "/sitemap.xml", "/help/extra"]
    assert f"{SITE}/help/extra" in [p["url"] for p in manifest["pages"]]


def test_a_refresh_does_not_fetch_a_sitemap_page_without_a_lastmod(tmp_path):
    entry = f"<url><loc>{SITE}/help/undated</loc></url>"

    manifest, _, requests, _ = refreshed(tmp_path, SITEMAP.replace("</urlset>", entry + "</urlset>"))

    assert requests == ["/robots.txt", "/sitemap.xml"]  # only a full crawl reads it
    assert f"{SITE}/help/undated" not in [p["url"] for p in manifest["pages"]]


def test_a_page_gone_from_the_sitemap_is_dropped_and_one_it_never_dated_is_kept(tmp_path):
    sitemap = "\n".join(line for line in SITEMAP.splitlines() if "/unlinked" not in line)

    manifest, _, requests, _ = refreshed(tmp_path, sitemap)

    urls = [p["url"] for p in manifest["pages"]]
    assert manifest["removed"] == [f"{SITE}/help/unlinked"]
    assert f"{SITE}/help/unlinked" not in urls
    assert {f"{SITE}/help/new", f"{SITE}/help/pay/card"} <= set(urls)  # no lastmod: kept
    assert requests == ["/robots.txt", "/sitemap.xml"]


def test_a_refresh_honours_robots_txt(tmp_path):
    sitemap = SITEMAP.replace("2026-08-15T09:00:00+10:00", "2026-09-20T09:00:00+10:00")
    robots = lambda _: httpx.Response(200, text="User-agent: *\nDisallow: /help/pay\n")  # noqa: E731

    manifest, _, requests, _ = refreshed(tmp_path, sitemap, {"/robots.txt": robots})

    assert "/help/pay" not in requests
    assert manifest["removed"] == [f"{SITE}/help/pay"]


def test_a_page_that_fails_to_download_keeps_its_previous_copy(tmp_path):
    sitemap = SITEMAP.replace("2026-08-15T09:00:00+10:00", "2026-09-20T09:00:00+10:00")

    def down(request):
        raise httpx.ConnectError("connection reset", request=request)

    manifest, _, _, _ = refreshed(tmp_path, sitemap, {"/help/pay": down})

    pay = next(p for p in manifest["pages"] if p["url"] == f"{SITE}/help/pay")
    assert pay["lastmod"] == "2026-08-15T09:00:00+10:00"  # the old copy, refetched next run
    assert manifest["removed"] == []


def test_being_refused_stops_a_refresh_and_keeps_the_record(tmp_path):
    sitemap = SITEMAP.replace("2026-08-15T09:00:00+10:00", "2026-09-20T09:00:00+10:00")
    refused = lambda _: httpx.Response(429, text="Slow down")  # noqa: E731

    with pytest.raises(Blocked):
        refreshed(tmp_path, sitemap, {"/help/pay": refused})

    assert "HTTP 429" in (tmp_path / "new" / "refused.txt").read_text()
    assert "snapshot_id" not in json.loads((tmp_path / "new" / "manifest.json").read_text())


def test_a_refresh_without_a_sitemap_stops_rather_than_dropping_every_page(tmp_path):
    missing = lambda _: httpx.Response(404)  # noqa: E731

    with pytest.raises(snapshot.NoSitemap):
        refreshed(tmp_path, overrides={"/sitemap.xml": missing})


def test_a_sitemap_url_skipped_last_time_is_not_asked_again_until_its_lastmod_changes(tmp_path):
    moved = f"<url><loc>{SITE}/help/moved</loc><lastmod>2026-06-01T09:00:00+10:00</lastmod></url>"
    sitemap = SITEMAP.replace("</urlset>", moved + "</urlset>")  # /moved is a 404 on the mock site

    manifest, previous, requests, _ = refreshed(tmp_path, sitemap, before=sitemap)

    assert {"url": f"{SITE}/help/moved", "reason": "HTTP 404"} in previous["not_saved"]
    assert requests == ["/robots.txt", "/sitemap.xml"]
    assert {"url": f"{SITE}/help/moved", "reason": "HTTP 404"} in manifest["not_saved"]

    _, _, requests, _ = refreshed(tmp_path / "later", sitemap.replace("2026-06-01", "2026-09-29"), before=sitemap)
    assert "/help/moved" in requests


def test_a_sitemap_of_old_addresses_that_redirect_is_refreshed_with_two_requests(tmp_path):
    # The sitemap lists /help/a.html, which redirects to /help/a (as many static sites do).
    sitemap = f"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>{SITE}/help/a.html</loc><lastmod>2026-04-23</lastmod></url></urlset>"""
    moved = lambda _: httpx.Response(301, headers={"location": f"{SITE}/help/a"})  # noqa: E731
    overrides = {"/sitemap.xml": xml(sitemap), "/help/a.html": moved, "/help/a": html(page()), "/help": html(page())}
    previous, _, _ = run(tmp_path / "prev", overrides=overrides)
    entry = next(p for p in previous["pages"] if p["url"] == f"{SITE}/help/a")
    assert entry["lastmod"] == "2026-04-23"  # the date the sitemap gives its old address

    client, requests = site(overrides)
    manifest = snapshot.refresh(
        Fetcher(client, sleep=lambda _: None),
        tmp_path / "prev",
        tmp_path / "new",
        site=SITE,
        sections=SECTIONS,
        log=lambda _: None,
    )

    assert requests == ["/robots.txt", "/sitemap.xml"]
    assert manifest["snapshot_id"] == previous["snapshot_id"]
