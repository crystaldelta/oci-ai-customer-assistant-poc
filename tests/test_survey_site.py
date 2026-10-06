"""Tests for the site survey. No network: a mock transport plays synthetic sites."""

import random

import httpx

from assistant import snapshot
from scripts import survey_site

SITE = "https://www.example.invalid"


def article(title: str, text: str) -> str:
    return (
        f"<html><head><title>{title} | Example Co</title></head><body>"
        "<header><nav><a href='/'>Home</a><a href='/help'>Help</a> Lots of menu text</nav></header>"
        f"<main><h1>{title}</h1><p>{text * 40}</p><details><summary>A question?</summary>"
        "<p>The answer.</p></details><table><tr><td>Rate</td></tr></table>"
        "<a href='/files/rates.pdf'>Rates (PDF)</a></main><footer>Footer</footer></body></html>"
    )


SPA = (
    '<html><head><title>App</title></head><body><div id="root"></div>'
    "<script src='/static/app.js'></script></body></html>"
)


def mock_site(pages: dict[str, str], robots: str = "User-agent: *\nAllow: /\n", sitemap: bool = True, status=None):
    requests: list[str] = []
    urls = "".join(f"<url><loc>{SITE}{p}</loc><lastmod>2026-09-01</lastmod></url>" for p in pages if p != "/")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append(path)
        if status and path in status:
            return httpx.Response(status[path], text="Access Denied", headers={"server": "AkamaiGHost"})
        if path == "/robots.txt":
            return httpx.Response(200, text=robots)
        if path == "/sitemap.xml" and sitemap:
            return httpx.Response(
                200, text=f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
            )
        if path in pages:
            headers = {"content-type": "text/html", "x-drupal-cache": "HIT", "set-cookie": "_abck=1; Path=/"}
            return httpx.Response(200, text=pages[path], headers=headers)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler)), requests


def survey(client, sections=("/",), sample=5):
    fetcher = snapshot.Fetcher(client, sleep=lambda _: None, rng=random.Random(1))
    return survey_site.survey_site(fetcher, SITE, list(sections), sample, log=lambda _: None)


def test_a_server_rendered_site_is_crawlable_and_the_report_suggests_settings():
    pages = {"/": article("Home", "Welcome. "), **{f"/help/q{i}": article(f"Q{i}", "An answer. ") for i in range(8)}}
    client, requests = mock_site(pages)

    result = survey(client, sections=["/help"], sample=4)
    text = survey_site.report(result)

    assert result.requests == len(requests) == 2 + 1 + 4  # robots, sitemap, home, sample
    assert survey_site.suggest_selector(result.pages) == "main"
    assert result.platforms == {"Drupal"} and result.protection == {"Akamai (Bot Manager)"}
    assert "## Verdict: Crawlable, with points to settle" in text
    assert "SITE_CONTENT_SELECTOR=main" in text and "SITE_SECTIONS=/help" in text
    assert "Bot protection (Akamai (Bot Manager))" in text
    assert "link to PDFs" in text
    assert "An answer." not in text  # no page content in the report


def test_email_addresses_hidden_from_crawlers_are_reported():
    page = article("Contact", "Write to us. ").replace(
        "</main>", '<a href="/cdn-cgi/l/email-protection#4f2c">[email&#160;protected]</a></main>'
    )
    client, _ = mock_site({"/": page})

    assert "Email addresses are hidden from crawlers" in survey_site.report(survey(client))


def test_a_site_built_in_the_browser_is_not_crawlable():
    client, _ = mock_site({"/": SPA, "/a": SPA, "/b": SPA})

    result = survey(client)

    assert all(p.rendered for p in result.pages)
    assert "Not crawlable without the client's help" in survey_site.report(result)


def test_a_refusal_stops_the_survey_at_once():
    client, requests = mock_site({"/": article("Home", "x")}, status={"/": 403})

    result = survey(client)

    assert requests == ["/robots.txt", "/sitemap.xml", "/"]
    assert result.blocked.startswith("HTTP 403")
    assert "The site refused the survey" in survey_site.report(result)


def test_robots_txt_rules_for_the_crawler_and_for_ai_bots_are_reported():
    robots = "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nDisallow: /help\n"
    client, requests = mock_site({"/": article("Home", "x"), "/help/a": article("A", "y")}, robots=robots)

    result = survey(client, sections=["/help"])

    assert result.robots_allows == {"/": True, "/help": False}
    assert result.ai_bot_rules == ["GPTBot"]
    assert "/help/a" not in requests  # disallowed pages are not read
    assert "robots.txt disallows the crawler for /help" in survey_site.report(result)


def test_without_a_sitemap_the_home_page_links_are_sampled():
    home = "<html><body><main><a href='/help/a'>A</a><a href='https://elsewhere.example/'>x</a></main></body></html>"
    client, requests = mock_site({"/": home, "/help/a": article("A", "y")}, sitemap=False)

    result = survey(client)

    assert "/help/a" in requests
    assert "No sitemap" in survey_site.report(result)


def test_the_sample_is_spread_across_sections():
    urls = [f"{SITE}/help/{i}" for i in range(10)] + [f"{SITE}/faq/{i}" for i in range(10)] + [f"{SITE}/blog/1"]

    picked = survey_site.pick_sample(urls, ["/help", "/faq", "/blog"], 5)

    assert len(picked) == 5
    assert {p.split("/")[3] for p in picked} == {"help", "faq", "blog"}
    assert picked == survey_site.pick_sample(urls, ["/help", "/faq", "/blog"], 5)  # the same each run


def test_on_a_flat_site_the_sample_is_not_the_first_pages_alphabetically():
    urls = [f"{SITE}/case-study-{i:02}" for i in range(20)] + [
        f"{SITE}/{p}" for p in ("about", "contact", "what-we-do")
    ]

    picked = survey_site.pick_sample(urls, ["/"], 10)

    assert len(picked) == 10
    assert picked != sorted(urls)[:10]
    assert any("case-study" not in p for p in picked)


def test_a_sitemap_of_redirects_and_one_generated_date_is_reported():
    # The sitemap lists /p0.html and so on, which redirect to /p0; every lastmod is the same day.
    urls = "".join(f"<url><loc>{SITE}/p{i}.html</loc><lastmod>2026-04-23</lastmod></url>" for i in range(4))

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if path == "/sitemap.xml":
            return httpx.Response(
                200, text=f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
            )
        if path.endswith(".html"):
            return httpx.Response(301, headers={"location": path.removesuffix(".html")})
        return httpx.Response(200, text=article("Page", "Text. "), headers={"content-type": "text/html"})

    text = survey_site.report(survey(httpx.Client(transport=httpx.MockTransport(handler))))

    assert "4 of 4 sampled sitemap URLs redirect (for example /p" in text
    assert "Every lastmod in the sitemap is the same day (2026-04-23)" in text


def test_the_sample_always_includes_a_contact_page():
    urls = [f"{SITE}/case-study-{i:02}" for i in range(20)] + [f"{SITE}/contact"]

    assert f"{SITE}/contact" in survey_site.pick_sample(urls, ["/"], 3)
