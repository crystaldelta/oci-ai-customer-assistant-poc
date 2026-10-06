"""Fetch a snapshot of the client's website, or refresh one (AD-24).

Only with the client's written permission (`docs/templates/crawl-permission.md`), and after the
survey (`scripts/survey_site.py`, docs/website-content.md) has shown the site can be read this way.

Starts from the section pages (`SITE_SECTIONS`, in priority order) and the sitemap's URLs in
those sections (sitemaps often miss pages), follows links that stay within the sections, and
saves each page's raw HTML with a manifest. The fetcher names itself in its user agent, obeys
`robots.txt`, waits a random 0.25-1.75 s before every request after the first (longer if
`robots.txt` asks), sends one request at a time, and stops at the first sign of being blocked: a
429, or a 403 the site did not produce itself. The refused response is saved to `refused.txt`
for review. If it is bot protection, ask the client to allow the crawler; never work around it.

A site may answer some pages (archived ones, say) with its own 403 page. Name a header that only
the site's own pages carry in `SITE_OWN_403_HEADER` (Drupal: `x-drupal-`), and such a 403 is
skipped, with the response saved to `refused/`, instead of stopping the crawl.

Output, under SNAPSHOT_DIR (`data/snapshots/`, git-ignored):

    <snapshot id>/manifest.json   what was fetched: URL, status, time, hash, file
    <snapshot id>/sitemap.json    each sitemap URL and its `lastmod` (null without one)
    <snapshot id>/pages/*.html    raw HTML, one file per page
    LATEST                        the ID of the newest complete snapshot

The snapshot ID is a hash of every saved page's URL and content hash, so two fetches of unchanged
content get the same ID. A crawl that stops early stays in a `.partial-*` directory and does not
move LATEST.

A refresh (`--refresh-from`, and the scheduled job in `refresh.py`) asks the site as little as it
can: `robots.txt`, the sitemap, and only the pages whose sitemap `lastmod` changed or that are new
to it. Every other page is copied from the previous snapshot, and no links are followed, so pages
found only through links wait for the next full crawl.

Usage:
    python -m assistant.snapshot                  # the settings' site and sections
    python -m assistant.snapshot --max-pages 10   # a quick trial
    python -m assistant.snapshot --resume data/snapshots/.partial-...   # continue after a stop
    python -m assistant.snapshot --refresh-from data/snapshots/<id>     # changed pages only
"""

import argparse
import gzip
import hashlib
import heapq
import json
import random
import shutil
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from itertools import count
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from assistant import config

ROBOT_NAME = "ai-starter-snapshot"
USER_AGENT = f"{ROBOT_NAME}/0.1 (Crystal Delta; content snapshot with the site owner's permission)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}

MIN_DELAY = 0.25  # seconds; a random wait in this range comes before each request
MAX_DELAY = 1.75
TIMEOUT = 30.0

MAX_PAGES = 500
MAX_DEPTH = 2  # link hops from a section page
MAX_SITEMAPS = 20  # child sitemaps read from a sitemap index

# Links to files, not pages. PDFs may hold content, but the cleaner reads HTML only.
_NOT_PAGES = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".doc", ".docx", ".xls", ".xlsx"}
_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def site_url() -> str:
    """`SITE_URL`, or the origin of `ALLOWED_LINK_PREFIX`: the site the answers link to."""
    parts = urlsplit(config.settings.site_url or config.settings.allowed_link_prefix)
    placeholder = not config.settings.site_url and parts.netloc.endswith("example.invalid")  # the starter's
    if parts.scheme != "https" or not parts.netloc or placeholder:
        raise SystemExit("Set SITE_URL (or ALLOWED_LINK_PREFIX) to the client's site, https://www.client.example")
    return f"https://{parts.netloc.lower()}"


def sections() -> list[str]:
    """`SITE_SECTIONS` as a list; the whole site (`/`) when unset."""
    found = ["/" + s.strip().strip("/") for s in config.settings.site_sections.split(",") if s.strip()]
    return found or ["/"]


class NoSitemap(Exception):
    """A refresh found no usable sitemap, so it cannot tell what changed."""


class Blocked(Exception):
    """The site refused the crawler (403 or 429). Carries the response for review."""

    def __init__(self, response: httpx.Response):
        super().__init__(f"{response.status_code} for {response.url}")
        self.response = response


def refusal_record(response: httpx.Response) -> str:
    """The refused response as text: status line, headers and body.

    Kept so a person can tell bot protection (a CDN's "Access Denied" page with a reference
    number) from the site's own 403 page before deciding whether to continue.
    """
    headers = "\n".join(f"{k}: {v}" for k, v in response.headers.items())
    return f"GET {response.url}\nHTTP {response.status_code}\n{headers}\n\n{response.text}"


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


def links(html: str, base: str) -> list[str]:
    """Every link on the page, made absolute."""
    parser = _Links()
    parser.feed(html)
    return [urljoin(base, href) for href in parser.hrefs]


def canonical(url: str, site: str) -> str | None:
    """The URL without query, fragment or trailing slash; None if it is off the site."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.netloc.lower() != urlsplit(site).netloc.lower():
        return None
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", parts.netloc.lower(), path, "", ""))


def section_of(url: str, sections: list[str]) -> int | None:
    """The index of the section the URL falls in, or None. The section `/` holds every page."""
    path = urlsplit(url).path
    return next((i for i, s in enumerate(sections) if s == "/" or path == s or path.startswith(s + "/")), None)


def in_sections(url: str, sections: list[str]) -> bool:
    return section_of(url, sections) is not None


def is_page(url: str) -> bool:
    return PurePosixPath(urlsplit(url).path).suffix.lower() not in _NOT_PAGES


def file_name(url: str) -> str:
    """`/help/faqs/late-fees` -> `help__faqs__late-fees.html`; the home page is `index.html`."""
    return (urlsplit(url).path.strip("/").replace("/", "__") or "index") + ".html"


def parse_sitemap(xml: bytes | str, site: str) -> tuple[dict[str, str | None], list[str]]:
    """A sitemap's pages (canonical URL to `lastmod`, None without one) and, for a sitemap index,
    its child sitemaps."""
    root = ET.fromstring(xml)
    pages: dict[str, str | None] = {}
    for entry in root.iter(f"{_SITEMAP_NS}url"):
        loc = entry.findtext(f"{_SITEMAP_NS}loc")
        url = canonical(loc.strip(), site) if loc else None
        if url:
            lastmod = entry.findtext(f"{_SITEMAP_NS}lastmod")
            pages[url] = lastmod.strip() if lastmod else None
    children = [loc.strip() for e in root.iter(f"{_SITEMAP_NS}sitemap") if (loc := e.findtext(f"{_SITEMAP_NS}loc"))]
    return pages, children


def snapshot_id(pages: list[dict]) -> str:
    lines = sorted(f"{p['url']} {p['sha256']}" for p in pages)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:12]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_manifest(out_dir: Path, manifest: dict, fetcher: "Fetcher") -> None:
    manifest["requests"] = fetcher.requests
    manifest["finished_at"] = _now()
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def record_refusal(out_dir: Path, manifest: dict, fetcher: "Fetcher", exc: "Blocked") -> None:
    """Keep the refused response and the partial manifest for review."""
    (out_dir / "refused.txt").write_text(refusal_record(exc.response))
    manifest["refused"] = {"url": str(exc.response.url), "status": exc.response.status_code, "file": "refused.txt"}
    write_manifest(out_dir, manifest, fetcher)


@dataclass
class Fetcher:
    """Sends every request, with the random wait before each one after the first.

    Follows redirects itself (the client must not), so each hop waits too. `own_403_header` is
    `SITE_OWN_403_HEADER`: a 403 carrying a header that starts with it is the site's own page.
    """

    client: httpx.Client
    sleep: Callable[[float], None] = time.sleep
    rng: random.Random = field(default_factory=random.Random)
    min_delay: float = MIN_DELAY
    max_delay: float = MAX_DELAY
    max_redirects: int = 5
    own_403_header: str = ""
    requests: int = 0

    def from_site(self, response: httpx.Response) -> bool:
        """True if the site itself produced the response, not the bot protection in front of it."""
        prefix = self.own_403_header.lower()
        return bool(prefix) and any(name.lower().startswith(prefix) for name in response.headers)

    def get(self, url: str) -> httpx.Response:
        for _ in range(self.max_redirects + 1):
            if self.requests:
                self.sleep(self.rng.uniform(self.min_delay, self.max_delay))
            self.requests += 1
            response = self.client.get(url)
            if response.status_code == 429 or (response.status_code == 403 and not self.from_site(response)):
                raise Blocked(response)
            if not response.is_redirect:
                return response
            url = str(response.next_request.url)
        raise httpx.TooManyRedirects(f"more than {self.max_redirects} redirects", request=response.request)


def new_fetcher(client: httpx.Client, **kwargs) -> Fetcher:
    """A fetcher with the settings' `SITE_OWN_403_HEADER`."""
    return Fetcher(client, own_403_header=config.settings.site_own_403_header, **kwargs)


def read_robots(fetcher: Fetcher, site: str) -> RobotFileParser:
    """Parse `robots.txt`; a Crawl-delay longer than our shortest wait raises the floor."""
    robots = RobotFileParser()
    response = fetcher.get(f"{site}/robots.txt")
    robots.parse(response.text.splitlines() if response.status_code == 200 else [])
    crawl_delay = robots.crawl_delay(ROBOT_NAME)
    if crawl_delay:
        fetcher.min_delay = max(fetcher.min_delay, float(crawl_delay))
        fetcher.max_delay = max(fetcher.max_delay, fetcher.min_delay)
    return robots


def fetch_sitemap(fetcher: Fetcher, site: str, robots: RobotFileParser) -> tuple[dict[str, str | None], list[str]]:
    """Every page in the site's sitemaps, and the sitemap URLs read.

    Starts from the `Sitemap:` lines in `robots.txt`, or `/sitemap.xml`; a sitemap index is
    followed one level, up to MAX_SITEMAPS children. A sitemap that fails or does not parse is
    left out.
    """
    queue = list(robots.site_maps() or [f"{site}/sitemap.xml"])
    pages: dict[str, str | None] = {}
    read: list[str] = []
    while queue and len(read) < MAX_SITEMAPS + 1:
        url = queue.pop(0)
        try:
            response = fetcher.get(url)
        except httpx.HTTPError:
            continue
        if response.status_code != 200:
            continue
        body = response.content
        if url.endswith(".gz") and body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        try:
            found, children = parse_sitemap(body, site)
        except ET.ParseError:
            continue
        read.append(url)
        pages.update(found)
        queue.extend(c for c in children if c not in read and c not in queue)
    return pages, read


def read_sitemap(fetcher: Fetcher, site: str, robots: RobotFileParser, out_dir: Path) -> dict[str, str | None]:
    """Save the sitemap's pages and return them. A resumed crawl reuses its saved copy."""
    saved = out_dir / "sitemap.json"
    if saved.exists():
        return json.loads(saved.read_text())["pages"]
    pages, read = fetch_sitemap(fetcher, site, robots)
    saved.write_text(json.dumps({"sitemaps": read, "pages": pages}, indent=2) + "\n")
    return pages


def skip_reason(
    url: str, final: str | None, response: httpx.Response, seen: set[str], sections: list[str]
) -> str | None:
    """Why a fetched page is not saved, or None to save it."""
    if response.status_code == 403:
        return "HTTP 403 (site page)"
    if response.status_code != 200:
        return f"HTTP {response.status_code}"
    if final is None or not in_sections(final, sections):
        return f"redirected out of scope to {response.url}"
    if final != url and final in seen:
        return f"redirected to {final}, already queued"
    if "html" not in response.headers.get("content-type", ""):
        return f"not HTML ({response.headers.get('content-type')})"
    return None


def crawl(
    fetcher: Fetcher,
    out_dir: Path,
    *,
    site: str,
    sections: list[str],
    max_pages: int = MAX_PAGES,
    max_depth: int = MAX_DEPTH,
    resume: bool = False,
    log: Callable[[str], None] = print,
) -> dict:
    """Fetch the snapshot into `out_dir` and return the manifest.

    Pages are fetched by depth, then by section in the order given, then in the order their
    links were found. Most pages carry navigation links into every section, so without the
    section order the pages that matter most could fall past `max_pages`. Raises `Blocked`
    (after writing the partial manifest) if the site refuses the crawler.

    With `resume`, `out_dir` holds a crawl that stopped early. The crawl runs again in the same
    order, but pages already saved are read from disk (their links included) and URLs already
    skipped stay skipped, so only the URL that was refused and the ones after it are requested.
    """
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "site": site,
        "user_agent": fetcher.client.headers.get("user-agent"),
        "sections": sections,
        "max_pages": max_pages,
        "max_depth": max_depth,
        "started_at": _now(),
        "pages": [],
        "not_saved": [],
    }
    saved: dict[str, dict] = {}  # from the crawl being resumed, by requested URL
    skipped: dict[str, str] = {}
    if resume:
        previous = json.loads((out_dir / "manifest.json").read_text())
        saved = {e.get("requested_url", e["url"]): e for e in previous["pages"]}
        skipped = {e["url"]: e["reason"] for e in previous["not_saved"]}
        manifest["started_at"] = previous["started_at"]
        manifest["resumed_at"] = [*previous.get("resumed_at", []), _now()]
        fetcher.requests = previous["requests"]  # the count covers the whole crawl
        (out_dir / "refused.txt").unlink(missing_ok=True)
        log(f"Resuming: {len(saved)} pages saved and {len(skipped)} skipped earlier")

    def not_saved(url: str, reason: str) -> None:
        manifest["not_saved"].append({"url": url, "reason": reason})
        log(f"  skip  {url} ({reason})")

    queue: list[tuple[int, int, int, str]] = []  # (depth, section, found, url): a heap
    seen: set[str] = set()
    order = count()

    def enqueue(url: str, depth: int) -> None:
        seen.add(url)
        heapq.heappush(queue, (depth, section_of(url, sections), next(order), url))

    def page_links(html: str, base: str) -> list[str]:
        """The page's links still to queue: on the site, in the sections, not files."""
        found = []
        for link in links(html, base):
            target = canonical(link, site)
            if (
                target
                and target not in seen
                and target not in found
                and in_sections(target, sections)
                and is_page(target)
            ):
                found.append(target)
        return found

    for section in sections:
        enqueue(canonical(site + section, site), 0)
    try:
        robots = read_robots(fetcher, site)
        lastmod = read_sitemap(fetcher, site, robots, out_dir)
        log(f"robots.txt read; the sitemap lists {len(lastmod)} URLs")
        for url in lastmod:
            if url not in seen and in_sections(url, sections) and is_page(url):
                enqueue(url, 1)

        while queue and len(manifest["pages"]) < max_pages:
            depth, _, _, url = heapq.heappop(queue)
            if url in skipped:
                not_saved(url, skipped.pop(url))
                continue
            if url in saved:
                entry = saved.pop(url)
                seen.add(entry["url"])
                manifest["pages"].append(entry)
                if depth < max_depth:
                    for target in page_links((out_dir / entry["file"]).read_text(), entry["url"]):
                        enqueue(target, depth + 1)
                continue
            if not robots.can_fetch(ROBOT_NAME, url):
                not_saved(url, "disallowed by robots.txt")
                continue
            try:
                response = fetcher.get(url)
            except httpx.HTTPError as exc:
                not_saved(url, f"{type(exc).__name__}: {exc}")
                continue
            final = canonical(str(response.url), site)
            reason = skip_reason(url, final, response, seen, sections)
            if reason:
                if response.status_code == 403:  # kept for review
                    (out_dir / "refused").mkdir(exist_ok=True)
                    (out_dir / "refused" / file_name(url).replace(".html", ".txt")).write_text(refusal_record(response))
                not_saved(url, reason)
                continue

            seen.add(final)
            name = file_name(final)
            (pages_dir / name).write_bytes(response.content)
            entry = {
                "url": final,
                "fetched_at": _now(),
                "sha256": hashlib.sha256(response.content).hexdigest(),
                "bytes": len(response.content),
                "file": f"pages/{name}",
                "depth": depth,
                # The sitemap may list the address that redirected here, not this one.
                "lastmod": lastmod.get(final) or lastmod.get(url),
            }
            if final != url:
                entry["requested_url"] = url
            manifest["pages"].append(entry)
            log(f"  {len(manifest['pages']):>3}  {final}")

            if depth < max_depth:
                for target in page_links(response.text, str(response.url)):
                    enqueue(target, depth + 1)
    except Blocked as exc:
        record_refusal(out_dir, manifest, fetcher, exc)
        raise

    manifest["unvisited"] = len(queue)
    manifest["snapshot_id"] = snapshot_id(manifest["pages"])
    write_manifest(out_dir, manifest, fetcher)
    return manifest


def refresh(
    fetcher: Fetcher,
    previous_dir: Path,
    out_dir: Path,
    *,
    site: str,
    sections: list[str],
    log: Callable[[str], None] = print,
) -> dict:
    """Refresh the snapshot in `previous_dir` into `out_dir` and return the manifest.

    Requests `robots.txt`, the sitemap and only the sitemap's pages (in the sections) whose
    `lastmod` differs from the previous snapshot's or that it did not have. Every other page is
    copied, so an unchanged site costs a request for `robots.txt` and one per sitemap, and gives
    the same snapshot ID. A URL the last fetch skipped (a redirect, a 404) is not asked again
    until its `lastmod` changes. A page the sitemap gives no `lastmod` is copied too: only a full
    crawl refreshes it. A page that had a `lastmod` and has left the sitemap (or now fails) is
    dropped and listed in `removed`; one that fails to download is copied. Raises `Blocked` as
    `crawl` does, and `NoSitemap` when no sitemap gives a `lastmod`, rather than dropping every page.
    """
    previous = json.loads((previous_dir / "manifest.json").read_text())
    by_url = {e["url"]: e for e in previous["pages"]}
    by_requested = {e.get("requested_url", e["url"]): e for e in previous["pages"]}
    skipped = {e["url"]: e["reason"] for e in previous.get("not_saved", [])}
    previous_sitemap = previous_dir / "sitemap.json"
    previous_lastmod = json.loads(previous_sitemap.read_text())["pages"] if previous_sitemap.exists() else {}
    (out_dir / "pages").mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "site": site,
        "user_agent": fetcher.client.headers.get("user-agent"),
        "sections": sections,
        "mode": "refresh",
        "previous": previous["snapshot_id"],
        "started_at": _now(),
        "pages": [],
        "not_saved": [],
        "carried": 0,
        "fetched": 0,
        "removed": [],
    }
    seen: set[str] = set()

    def carry(entry: dict) -> None:
        shutil.copyfile(previous_dir / entry["file"], out_dir / entry["file"])
        manifest["pages"].append(entry)
        manifest["carried"] += 1
        seen.add(entry["url"])

    try:
        robots = read_robots(fetcher, site)
        lastmod = read_sitemap(fetcher, site, robots, out_dir)
        if not any(lastmod.values()):
            raise NoSitemap(f"no sitemap URLs with lastmod for {site}")
        for url in lastmod:
            if not (in_sections(url, sections) and is_page(url)) or url in seen:
                continue
            prev = by_requested.get(url) or by_url.get(url)
            # `url` is the sitemap's address, which may redirect to the page's: compare its date.
            if not lastmod[url] or (prev and prev.get("lastmod") == lastmod[url]):
                if prev and prev["url"] not in seen:  # undated or unchanged: copied
                    carry(prev)
                continue
            if url in skipped and previous_lastmod.get(url) == lastmod[url]:
                manifest["not_saved"].append({"url": url, "reason": skipped[url]})  # unchanged: not asked
                continue
            if not robots.can_fetch(ROBOT_NAME, url):
                manifest["not_saved"].append({"url": url, "reason": "disallowed by robots.txt"})
                continue
            try:
                response = fetcher.get(url)
            except httpx.HTTPError as exc:
                if prev and prev["url"] not in seen:
                    log(f"  kept  {url} ({type(exc).__name__}; the previous copy)")
                    carry(prev)
                else:
                    manifest["not_saved"].append({"url": url, "reason": f"{type(exc).__name__}: {exc}"})
                continue
            final = canonical(str(response.url), site)
            reason = skip_reason(url, final, response, seen, sections)
            if reason:
                manifest["not_saved"].append({"url": url, "reason": reason})
                log(f"  skip  {url} ({reason})")
                continue
            seen.add(final)
            name = file_name(final)
            (out_dir / "pages" / name).write_bytes(response.content)
            entry = {
                "url": final,
                "fetched_at": _now(),
                "sha256": hashlib.sha256(response.content).hexdigest(),
                "bytes": len(response.content),
                "file": f"pages/{name}",
                "depth": prev.get("depth") if prev else None,
                "lastmod": lastmod.get(final) or lastmod[url],
            }
            if final != url:
                entry["requested_url"] = url
            manifest["pages"].append(entry)
            manifest["fetched"] += 1
            log(f"  new   {final}" if prev is None else f"  changed  {final}")
    except Blocked as exc:
        record_refusal(out_dir, manifest, fetcher, exc)
        raise

    for entry in previous["pages"]:
        if entry["url"] in seen:
            continue
        if entry.get("lastmod"):
            manifest["removed"].append(entry["url"])
            log(f"  removed  {entry['url']}")
        else:
            carry(entry)
    manifest["snapshot_id"] = snapshot_id(manifest["pages"])
    write_manifest(out_dir, manifest, fetcher)
    return manifest


def finish(partial: Path, root: Path, manifest: dict) -> Path:
    """Move a completed snapshot to its ID's folder under `root` and make it LATEST."""
    final = root / manifest["snapshot_id"]
    if final.exists():  # same content as an earlier snapshot; keep the newer fetch times
        shutil.rmtree(final)
    partial.rename(final)
    (root / "LATEST").write_text(manifest["snapshot_id"] + "\n")
    return final


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    parser.add_argument("--max-depth", type=int, default=MAX_DEPTH)
    parser.add_argument("--out", type=Path, default=config.settings.snapshot_dir)
    start = parser.add_mutually_exclusive_group()
    start.add_argument(
        "--resume",
        type=Path,
        metavar="PARTIAL_DIR",
        help="continue a crawl that stopped early (a .partial-* directory)",
    )
    start.add_argument(
        "--refresh-from",
        type=Path,
        metavar="SNAPSHOT_DIR",
        help="fetch only the pages whose sitemap lastmod changed since this snapshot",
    )
    args = parser.parse_args()

    site, scope = site_url(), sections()
    root = args.out
    for given in (args.resume, args.refresh_from):
        if given and not (given / "manifest.json").exists():
            parser.error(f"no manifest.json in {given}")
    partial = args.resume or root / f".partial-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    print(f"Site {site}, sections {', '.join(scope)}")
    with httpx.Client(headers=HEADERS, timeout=TIMEOUT) as client:
        fetcher = new_fetcher(client)
        try:
            if args.refresh_from:
                manifest = refresh(fetcher, args.refresh_from, partial, site=site, sections=scope)
            else:
                manifest = crawl(
                    fetcher,
                    partial,
                    site=site,
                    sections=scope,
                    max_pages=args.max_pages,
                    max_depth=args.max_depth,
                    resume=bool(args.resume),
                )
        except Blocked as exc:
            print(f"\nStopped: the site refused the crawler ({exc}) after {fetcher.requests} requests.")
            print(f"The refused response is in {partial / 'refused.txt'}; review it before continuing.")
            print("If it is bot protection, ask the client to allow the crawler; do not retry around it.")
            return 1
        except NoSitemap as exc:
            print(f"\nStopped: {exc}. Nothing changed; a full crawl does not need the sitemap.")
            return 1

    final = finish(partial, root, manifest)
    if args.refresh_from:
        print(
            f"\nSnapshot {manifest['snapshot_id']}: {manifest['fetched']} pages fetched, {manifest['carried']} "
            f"copied, {len(manifest['removed'])} removed, {manifest['requests']} requests. In {final}"
        )
        return 0
    print(
        f"\nSnapshot {manifest['snapshot_id']}: {len(manifest['pages'])} pages saved, "
        f"{len(manifest['not_saved'])} not saved, {manifest['unvisited']} found but not visited "
        f"(--max-pages), {manifest['requests']} requests. In {final}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
