"""Survey a website before crawling it: can the crawler read it, and how? (docs/website-content.md)

A quick, polite scan for the developer, run once before the crawl is planned or the client is
asked for permission to crawl. It reads `robots.txt`, the sitemaps and a small sample of pages,
with the crawler's own user agent, waits and stop on refusal (`assistant.snapshot`): at most
`--sample` pages plus the home page, `robots.txt` and up to 21 sitemaps. It saves no page
content, only the report:

- robots.txt: whether the crawler may read the site and its sections, a crawl delay, AI-bot rules
- sitemap: where, how many URLs, how many with `lastmod` (which a cheap refresh depends on),
  the top-level sections
- platform and bot protection, from headers, cookies and markup
- each sampled page: status, size, text in the HTML (or rendered by script), title, noindex
- the main content selector to set (`SITE_CONTENT_SELECTOR`), collapsed answers, tables, PDFs
- a verdict, suggested settings and questions for the client

The report goes to data/survey/<host>-<UTC time>.md (git-ignored) and the terminal. Copy what
holds into the project's docs/site.md, and read a few sampled pages by eye before trusting the
suggested selector.

Usage:
    .venv/bin/python scripts/survey_site.py https://www.client.example
    .venv/bin/python scripts/survey_site.py https://www.client.example --sections /help,/faq --sample 8
"""

import argparse
import random
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assistant import config, snapshot  # noqa: E402

SAMPLE = 10
MAX_SAMPLE = 20
# Tried in this order; the first that is on most pages and holds the article without the
# navigation is suggested.
CANDIDATES = ["main", "[role=main]", "article", "#main-content", "#content", ".main-content", ".content"]
AI_BOTS = ("GPTBot", "ClaudeBot", "anthropic-ai", "CCBot", "Google-Extended", "PerplexityBot", "Applebot-Extended")
THIN_TEXT = 500  # characters: a page with less text than this in its HTML may be rendered by script

# (name, header names, header value fragments, cookie prefixes)
PROTECTION = [
    ("Akamai (Bot Manager)", (), ("akamaighost",), ("_abck", "bm_sz", "ak_bmsc")),
    ("Cloudflare", ("cf-ray",), ("cloudflare",), ("__cf_bm", "cf_clearance")),
    ("Imperva Incapsula", ("x-iinfo",), ("incapsula",), ("incap_ses", "visid_incap")),
    ("DataDome", ("x-datadome",), (), ("datadome",)),
    ("HUMAN (PerimeterX)", (), (), ("_px",)),
    ("Sucuri", ("x-sucuri-id",), ("sucuri",), ()),
]
# (name, header names, header value fragments, markup fragments)
PLATFORMS = [
    ("Drupal", ("x-drupal-cache", "x-drupal-dynamic-cache"), ("drupal",), ("drupal-settings-json",)),
    ("WordPress", (), ("wordpress",), ("/wp-content/", "/wp-json/")),
    ("Adobe Experience Manager", (), (), ("/etc.clientlibs/", "/content/dam/")),
    ("Sitecore", (), ("sitecore",), ("/-/media/",)),
    ("Next.js", ("x-nextjs-cache",), ("next.js",), ("__NEXT_DATA__", "/_next/static/")),
    ("Nuxt", (), (), ("__NUXT__", "/_nuxt/")),
    ("Gatsby", (), (), ("___gatsby",)),
    ("Angular", (), (), ("ng-version=",)),
    ("Webflow", (), (), ("data-wf-site",)),
    ("Squarespace", (), ("squarespace",), ("static1.squarespace.com",)),
    ("Wix", (), ("wix",), ("static.wixstatic.com",)),
    ("Shopify", (), ("shopify",), ("cdn.shopify.com",)),
    ("Zendesk Guide", (), (), ("zdassets.com",)),
    ("CloudFront", ("x-amz-cf-id",), ("cloudfront",), ()),
    ("Fastly", ("x-fastly-request-id",), ("fastly",), ()),
    ("Vercel", ("x-vercel-id",), (), ()),
    ("Netlify", ("x-nf-request-id",), (), ()),
]
# Markup left by a page that builds its content in the browser.
SPA_ROOTS = ('<div id="root"></div>', '<div id="app"></div>', '<div id="__next"></div>', "<app-root></app-root>")


@dataclass
class Page:
    url: str
    status: int
    final: str = ""
    bytes: int = 0
    text: int = 0  # characters of text in the body, scripts and navigation removed
    title: str = ""
    h1: bool = False
    noindex: bool = False
    rendered: bool = False  # little text, and markup of a script-built page
    candidates: dict[str, int] = field(default_factory=dict)  # selector: text characters, when present
    collapsed: int = 0  # <details>, hidden elements and accordion triggers
    tables: int = 0
    pdfs: int = 0
    forms: int = 0
    iframes: int = 0
    hidden_emails: bool = False  # email addresses replaced by a script (Cloudflare's obfuscation)
    note: str = ""


@dataclass
class Survey:
    site: str
    sections: list[str]
    started: str
    robots_status: int | None = None
    robots_allows: dict[str, bool] = field(default_factory=dict)  # path: may the crawler read it
    crawl_delay: float | None = None
    ai_bot_rules: list[str] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)
    sitemap: dict[str, str | None] = field(default_factory=dict)
    pages: list[Page] = field(default_factory=list)
    protection: set[str] = field(default_factory=set)
    platforms: set[str] = field(default_factory=set)
    blocked: str = ""  # the refusal, if the site refused the survey
    requests: int = 0
    seconds: float = 0.0


# ------------------------------------------------------------------ detection


def detect(response: httpx.Response, html: str, survey: Survey) -> None:
    """Add the platform and bot protection the response shows to `survey`."""
    headers = {k.lower(): v.lower() for k, v in response.headers.items()}
    values = " ".join(headers.values())
    cookies = [c.split("=", 1)[0].strip().lower() for c in response.headers.get_list("set-cookie")]
    for name, names, fragments, prefixes in PROTECTION:
        if (
            any(n in headers for n in names)
            or any(f in values for f in fragments)
            or any(c.startswith(p.lower()) for c in cookies for p in prefixes)
        ):
            survey.protection.add(name)
    for name, names, fragments, markup in PLATFORMS:
        if any(n in headers for n in names) or any(f in values for f in fragments) or any(m in html for m in markup):
            survey.platforms.add(name)


def text_length(el) -> int:
    return len(" ".join(el.get_text(" ").split())) if el is not None else 0


def analyse(url: str, response: httpx.Response, survey: Survey) -> Page:
    html = response.text
    detect(response, html, survey)
    page = Page(url, response.status_code, final=str(response.url), bytes=len(response.content))
    if response.status_code != 200 or "html" not in response.headers.get("content-type", ""):
        page.note = f"HTTP {response.status_code}" if response.status_code != 200 else "not HTML"
        return page
    soup = BeautifulSoup(html, "html.parser")
    page.title = " ".join(soup.title.get_text().split()) if soup.title else ""
    page.h1 = soup.find("h1") is not None
    robots_meta = soup.find("meta", attrs={"name": "robots"})
    page.noindex = bool(robots_meta and "noindex" in robots_meta.get("content", "").lower())
    page.collapsed = len(soup.find_all("details")) + len(soup.select("[hidden]")) + len(soup.select("[aria-expanded]"))
    page.tables = len(soup.find_all("table"))
    page.pdfs = sum(1 for a in soup.find_all("a", href=True) if urlsplit(a["href"]).path.lower().endswith(".pdf"))
    page.forms = len(soup.find_all("form"))
    page.iframes = len(soup.find_all("iframe"))
    page.hidden_emails = "/cdn-cgi/l/email-protection" in html or "__cf_email__" in html
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    for selector in CANDIDATES:
        if found := soup.select_one(selector):
            page.candidates[selector] = text_length(found)
    for tag in soup(["nav", "header", "footer", "aside"]):
        tag.decompose()
    page.text = text_length(soup.body)
    page.rendered = page.text < THIN_TEXT and any(marker in html for marker in SPA_ROOTS + ("__NEXT_DATA__",))
    return page


def suggest_selector(pages: list[Page]) -> str | None:
    """The first candidate on at least 80% of the pages that holds most of their text."""
    readable = [p for p in pages if p.status == 200 and p.text]
    for selector in CANDIDATES:
        with_it = [p for p in readable if selector in p.candidates]
        if readable and len(with_it) >= 0.8 * len(readable):
            share = statistics.median(min(p.candidates[selector] / p.text, 1.0) for p in with_it)
            if share >= 0.5:
                return selector
    return None


# ------------------------------------------------------------------ sampling


def pick_sample(urls: list[str], sections: list[str], n: int) -> list[str]:
    """Up to `n` URLs spread across the sections (or the top-level paths), round robin; within
    each, a fixed shuffle, so the same sitemap gives the same sample. Top-level paths (no
    sections given) are shuffled too: on a flat site each page is its own group."""
    groups: dict[str, list[str]] = {}
    for url in sorted(urls):
        index = snapshot.section_of(url, sections)
        if index is None:
            continue
        key = sections[index] if sections != ["/"] else "/" + urlsplit(url).path.strip("/").partition("/")[0]
        groups.setdefault(key, []).append(url)
    rng = random.Random(0)
    for group in groups.values():
        rng.shuffle(group)
    ordered = list(groups.values())
    if sections == ["/"]:
        rng.shuffle(ordered)
    picked: list[str] = []
    for step in range(max((len(g) for g in ordered), default=0)):
        picked += [g[step] for g in ordered if len(g) > step]
    picked = picked[:n]
    # A contact page holds the phone numbers and email addresses an assistant is asked for most.
    contact = next((u for g in ordered for u in sorted(g) if "contact" in urlsplit(u).path.lower()), None)
    if contact and contact not in picked and picked:
        picked[-1] = contact
    return picked


# ------------------------------------------------------------------ the survey


def survey_site(fetcher: snapshot.Fetcher, site: str, sections: list[str], sample: int, log=print) -> Survey:
    started = datetime.now().astimezone()
    result = Survey(site=site, sections=sections, started=f"{started:%-d %b %Y, %H:%M %Z}")
    began = time.monotonic()
    try:
        response = fetcher.get(f"{site}/robots.txt")
        result.robots_status = response.status_code
        robots_text = response.text if response.status_code == 200 else ""
        robots = RobotFileParser()
        robots.parse(robots_text.splitlines())
        result.crawl_delay = robots.crawl_delay(snapshot.ROBOT_NAME)
        result.ai_bot_rules = [bot for bot in AI_BOTS if bot.lower() in robots_text.lower()]
        for path in ["/", *(s for s in sections if s != "/")]:
            result.robots_allows[path] = robots.can_fetch(snapshot.ROBOT_NAME, site + path)
        log(f"robots.txt: HTTP {response.status_code}")

        result.sitemap, result.sitemaps = snapshot.fetch_sitemap(fetcher, site, robots)
        log(f"sitemap: {len(result.sitemap)} URLs from {len(result.sitemaps)} sitemap(s)")

        home = fetcher.get(f"{site}/")
        result.pages.append(analyse(f"{site}/", home, result))
        candidates = [u for u in result.sitemap if snapshot.is_page(u)]
        if not candidates:  # no sitemap: the home page's links
            found = (snapshot.canonical(link, site) for link in snapshot.links(home.text, str(home.url)))
            candidates = sorted({u for u in found if u and snapshot.is_page(u)})
        for url in pick_sample([u for u in candidates if u != f"{site}/"], sections, sample):
            if not robots.can_fetch(snapshot.ROBOT_NAME, url):
                result.pages.append(Page(url, 0, note="disallowed by robots.txt"))
                continue
            try:
                result.pages.append(analyse(url, fetcher.get(url), result))
            except httpx.HTTPError as exc:
                result.pages.append(Page(url, 0, note=type(exc).__name__))
            log(f"  {result.pages[-1].status or '-'}  {url}")
    except snapshot.Blocked as exc:
        detect(exc.response, exc.response.text, result)
        result.blocked = f"HTTP {exc.response.status_code} for {exc.response.url}, request {fetcher.requests}"
        log(f"Stopped: the site refused the survey ({result.blocked})")
    result.requests = fetcher.requests
    result.seconds = time.monotonic() - began
    return result


# ------------------------------------------------------------------ the report


def verdict(s: Survey, selector: str | None) -> tuple[str, list[str], list[str]]:
    """The verdict, the reasons that block a crawl, and the points to settle."""
    blockers, points = [], []
    readable = [p for p in s.pages if p.status == 200 and not p.note]
    if s.blocked:
        blockers.append(f"The site refused the survey ({s.blocked}). Ask the client to allow the crawler.")
    if not all(s.robots_allows.values()):
        closed = [p for p, ok in s.robots_allows.items() if not ok]
        blockers.append(f"robots.txt disallows the crawler for {', '.join(closed)}.")
    rendered = [p for p in readable if p.rendered]
    if readable and len(rendered) > len(readable) / 2:
        blockers.append(
            f"{len(rendered)} of {len(readable)} pages build their content in the browser: the crawler reads "
            "HTML only. Ask for a CMS export or an API, or record a decision to render pages (a headless browser)."
        )
    if s.protection:
        points.append(
            f"Bot protection ({', '.join(sorted(s.protection))}) did not stop the survey, but may stop a longer "
            "crawl. Name the user agent in the permission request and ask whether it should be allowed."
        )
    dates = [v for v in s.sitemap.values() if v]
    if not s.sitemap:
        points.append("No sitemap: every refresh is a full crawl. Ask whether one can be published.")
    elif len(dates) < 0.5 * len(s.sitemap):
        points.append("Under half the sitemap's URLs have a lastmod: a refresh cannot tell what changed for the rest.")
    elif len(dates) > 3 and len({d[:10] for d in dates}) == 1:
        points.append(
            f"Every lastmod in the sitemap is the same day ({dates[0][:10]}): the dates are likely generated, not "
            "kept up to date, so a refresh cannot tell what changed. Plan on full crawls, or ask for real dates."
        )
    in_sitemap = [p for p in readable if p.url in s.sitemap and p.final]
    moved = [p for p in in_sitemap if snapshot.canonical(p.final, s.site) != p.url]
    if in_sitemap and len(moved) > len(in_sitemap) / 2:
        example = f"{urlsplit(moved[0].url).path} to {urlsplit(moved[0].final).path}"
        points.append(
            f"{len(moved)} of {len(in_sitemap)} sampled sitemap URLs redirect (for example {example}): the sitemap "
            "lists old addresses, so each page costs two requests. Ask whether it can list the final addresses."
        )
    if readable and not selector:
        points.append("No common content element found: inspect a page and set SITE_CONTENT_SELECTOR by hand.")
    if any(p.noindex for p in readable):
        points.append("Some sampled pages are marked noindex: confirm with the client that they are in scope.")
    if s.ai_bot_rules:
        points.append(
            f"robots.txt has rules for AI crawlers ({', '.join(s.ai_bot_rules)}): the site owner has a stated "
            "position on AI use. Raise it in the permission request."
        )
    if s.crawl_delay:
        points.append(f"robots.txt asks for a {s.crawl_delay} s crawl delay; the crawler waits at least that.")
    if any(p.hidden_emails for p in readable):
        points.append(
            "Email addresses are hidden from crawlers (Cloudflare's email obfuscation): the content will have no "
            "email addresses, so the assistant cannot give them. Ask whether to turn it off for the crawler, or "
            "supply the addresses another way."
        )
    if any(p.pdfs for p in readable):
        points.append("Pages link to PDFs, which the cleaner does not read. Ask whether any hold answers.")
    if blockers:
        return "Not crawlable without the client's help", blockers, points
    return ("Crawlable, with points to settle" if points else "Crawlable"), blockers, points


def report(s: Survey) -> str:
    selector = suggest_selector(s.pages)
    title, blockers, points = verdict(s, selector)
    readable = [p for p in s.pages if p.status == 200 and not p.note]
    host = urlsplit(s.site).netloc
    lines = [
        f"# {host}: site survey",
        "",
        f"Surveyed {s.started} by `scripts/survey_site.py`: {s.requests} requests in {s.seconds:.0f} s, as "
        f"`{snapshot.USER_AGENT}`. Sections: {', '.join(s.sections)}.",
        "",
        f"## Verdict: {title}",
        "",
        *[f"- **Blocks a crawl:** {b}" for b in blockers],
        *[f"- {p}" for p in points],
        *(["- Nothing to settle."] if not blockers and not points else []),
        "",
        "## Suggested settings",
        "",
        "```bash",
        f"SITE_URL={s.site}",
        f"SITE_SECTIONS={','.join(x for x in s.sections if x != '/') or '<the sections to answer from>'}",
        f"SITE_CONTENT_SELECTOR={selector or '<set by hand>'}",
        f"ALLOWED_LINK_PREFIX={s.site}/",
        "```",
        "",
        "Add `SITE_OWN_403_HEADER` only if the crawl meets the site's own 403 pages (docs/website-content.md).",
        "",
        "## robots.txt",
        "",
        f"- HTTP {s.robots_status}" if s.robots_status is not None else "- Not read",
        *[f"- `{p}`: {'allowed' if ok else '**disallowed**'} for the crawler" for p, ok in s.robots_allows.items()],
        f"- Crawl delay: {s.crawl_delay or 'none'}",
        f"- Rules for AI crawlers: {', '.join(s.ai_bot_rules) or 'none'}",
        "",
        "## Sitemap",
        "",
    ]
    if s.sitemap:
        dated = sorted(v for v in s.sitemap.values() if v)
        tops = Counter("/" + urlsplit(u).path.strip("/").partition("/")[0] for u in s.sitemap)
        in_scope = sum(1 for u in s.sitemap if snapshot.in_sections(u, s.sections))
        lines += [
            f"- {len(s.sitemap)} URLs from {len(s.sitemaps)} sitemap(s): {', '.join(f'`{m}`' for m in s.sitemaps)}",
            f"- {len(dated)} with a `lastmod`" + (f", from {dated[0][:10]} to {dated[-1][:10]}" if dated else ""),
            f"- {in_scope} in the sections",
            "- Top-level paths: " + ", ".join(f"`{t}` {n}" for t, n in tops.most_common(15)),
        ]
    else:
        lines.append("- None found (robots.txt `Sitemap:` lines, then `/sitemap.xml`)")
    lines += [
        "",
        "## Platform and protection",
        "",
        f"- Platform: {', '.join(sorted(s.platforms)) or 'not recognised'}",
        f"- Bot protection: {', '.join(sorted(s.protection)) or 'none seen'}",
        "",
        "## Pages sampled",
        "",
        "| Page | Status | KB | Text chars | Title | Notes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for p in s.pages:
        notes = [p.note] if p.note else []
        if p.final and p.final.rstrip("/") != p.url.rstrip("/"):
            notes.append(f"redirected to {urlsplit(p.final).path}")
        if p.rendered:
            notes.append("rendered by script")
        if p.noindex:
            notes.append("noindex")
        if p.status == 200 and not p.h1:
            notes.append("no h1")
        path = urlsplit(p.url).path or "/"
        title = p.title.replace("|", "\\|")[:60]
        lines.append(f"| `{path}` | {p.status or '-'} | {p.bytes // 1024} | {p.text} | {title} | {'; '.join(notes)} |")
    if readable:
        lines += ["", "## Page structure", ""]
        for selector_name in CANDIDATES:
            with_it = [p for p in readable if selector_name in p.candidates]
            if with_it:
                share = statistics.median(min(p.candidates[selector_name] / p.text, 1.0) for p in with_it if p.text)
                lines.append(
                    f"- `{selector_name}` on {len(with_it)} of {len(readable)} pages, holding a median "
                    f"{share:.0%} of the page's text"
                )
        lines += [
            f"- Collapsed or hidden elements (FAQ answers, menus): {sum(p.collapsed for p in readable)}",
            f"- Tables: {sum(p.tables for p in readable)}; forms: {sum(p.forms for p in readable)}; "
            f"embedded frames: {sum(p.iframes for p in readable)}; links to PDFs: {sum(p.pdfs for p in readable)}",
        ]
    lines += [
        "",
        "## Check by hand",
        "",
        "- Open two or three sampled pages and compare what a visitor sees with the text in the HTML "
        "(view source): FAQ answers, tables and tabs must be in it.",
        "- Run the cleaner on a short trial crawl (`--max-pages 10`) and read the Markdown.",
        "- Note content that differs by region, customer type or sign-in, and where the site keeps it.",
        "",
        "## Questions for the client",
        "",
        "- Which sections should the assistant answer from, and which are out of scope?",
        "- Should the crawler's user agent be allowed through the bot protection, or from a fixed address?",
        "- Is there a CMS export or API that would be better than crawling?",
        "- Are answers kept anywhere the crawler cannot read (PDFs, sign-in pages, tools)?",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("site", nargs="?", help="the site's address (default: SITE_URL)")
    parser.add_argument("--sections", default=None, help="path prefixes, comma-separated (default: SITE_SECTIONS)")
    parser.add_argument("--sample", type=int, default=SAMPLE, help=f"pages to read, up to {MAX_SAMPLE}")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "survey")
    args = parser.parse_args(argv)
    if args.site:
        config.settings.site_url = args.site
    if args.sections is not None:
        config.settings.site_sections = args.sections
    site, sections = snapshot.site_url(), snapshot.sections()
    sample = max(1, min(args.sample, MAX_SAMPLE))

    with httpx.Client(headers=snapshot.HEADERS, timeout=snapshot.TIMEOUT) as client:
        result = survey_site(snapshot.new_fetcher(client), site, sections, sample)
    text = report(result)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{urlsplit(site).netloc}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.md"
    path.write_text(text)
    print("\n" + text)
    print(f"Saved to {path}")
    return 1 if result.blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
