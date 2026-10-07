"""Tests for turning snapshot HTML into clean Markdown. No network, no snapshot needed."""

import json

from assistant.clean import clean_page, convert, section_of
from assistant.ingest import read_page


def page(body: str, title: str = "Late fees | Example Co", heading: str = "Late fees") -> str:
    """A page shaped like most content sites: header, navigation and footer around `<main>`."""
    return f"""<html><head><title>{title}</title></head><body>
      <header class="site-header"><nav><a href="/">Home</a> Menu text</nav></header>
      <main><h1>{heading}</h1>{body}<aside>Related: something else</aside></main>
      <footer>Copyright Example Co</footer>
    </body></html>"""


def test_only_the_main_content_is_kept():
    title, body = clean_page(page("<p>You may be charged a late fee.</p>"))

    assert title == "Late fees"
    assert body == "You may be charged a late fee."


def test_the_first_matching_selector_wins_and_the_body_is_the_fallback():
    html = (
        "<html><body><header>Site menu</header><div class='content'><p>The article.</p></div>"
        "<div class='promo'><p>A promotion.</p></div></body></html>"
    )

    assert clean_page(html, selector="#missing, .content")[1] == "The article."
    assert clean_page(html, selector="main")[1] == "The article.\n\nA promotion."  # no header


def test_skip_links_are_dropped_when_the_body_is_the_fallback():
    html = (
        '<html><body><a href="#main-content" class="skip-link">Skip to main content</a>'
        '<div><a href="#top">Skip to content</a></div><p>The article.</p></body></html>'
    )

    assert clean_page(html, selector="main")[1] == "The article."


def test_collapsed_answers_are_kept_under_their_summaries():
    html = page(
        "<h2>Questions</h2>"
        "<details><summary>When is a fee charged?</summary><p>After the due date.</p></details>"
        '<details><summary>Can it be waived?</summary><div hidden=""><p>Talk to us.</p>'
        "<h4>Hardship</h4><p>We can help.</p></div></details>"
        "<p>After the group.</p>"
    )

    assert clean_page(html)[1].split("\n\n") == [
        "## Questions",
        "### When is a fee charged?",
        "After the due date.",
        "### Can it be waived?",
        "Talk to us.",
        "#### Hardship",
        "We can help.",
        "After the group.",
    ]


def test_tables_become_markdown():
    html = page(
        "<table><thead><tr><th>Plan</th><th>1 July</th></tr></thead><tbody>"
        '<tr><td colspan="2"><strong>Variable plans</strong></td></tr>'
        "<tr><td>Standard | Home</td><td>Yes</td></tr>"
        "<tr><td>Rebate</td><td><ul><li>$800 a year</li><li>$1.02 a day</li></ul></td></tr>"
        "</tbody></table>"
    )

    assert clean_page(html)[1].splitlines() == [
        "| Plan | 1 July |",
        "| --- | --- |",
        "| Variable plans |  |",
        "| Standard \\| Home | Yes |",
        "| Rebate | $800 a year; $1.02 a day |",
    ]


def test_lists_keep_their_nesting_and_numbering():
    html = page("<ol><li>Log in<ul><li>on the app</li></ul></li><li>Pay</li></ol>")

    assert clean_page(html)[1] == "1. Log in\n  - on the app\n2. Pay"


def test_forms_frames_images_and_screen_reader_text_are_dropped():
    html = page(
        "<p>Write to us.</p>"
        "<div><form><label>First name</label><input/></form></div>"
        '<iframe src="https://maps.example/"></iframe>'
        '<p><img alt="banner"/><span class="visually-hidden">Warning message</span>Careful.</p>'
        "<script>var x = 1;</script>"
    )

    assert clean_page(html)[1] == "Write to us.\n\nCareful."


def test_phone_links_show_the_number_and_bare_buttons_are_dropped():
    html = page(
        '<p>Call <a href="tel:1300000000">our team</a> or <a href="tel:1300 000 000">1300 000 000</a>.</p>'
        '<p><a class="button" href="/x">Find out more</a></p>'
        '<div><a href="/x">Learn more</a></div>'
        "<p>Report a fault to your provider.</p>"
    )

    assert clean_page(html)[1] == "Call our team (1300000000) or 1300 000 000.\n\nReport a fault to your provider."


def test_a_banner_repeating_the_title_is_dropped():
    html = page("<div class='banner'><h2>Late fees</h2><p>Intro.</p></div>")

    assert clean_page(html)[1] == "Intro."


def test_the_title_falls_back_to_the_title_tag_without_the_site_name():
    html = "<html><head><title>Opt out | Example Co</title></head><body><main><p>x</p></main></body></html>"

    assert clean_page(html)[0] == "Opt out"


def test_section_of_ignores_the_whole_site_section():
    sections = ["/help", "/"]

    assert section_of("https://www.example.invalid/help/pay", sections) == "/help"
    assert section_of("https://www.example.invalid/plans", sections) is None


def test_convert_writes_pages_that_ingest_can_read(tmp_path):
    snapshot = tmp_path / "snap"
    (snapshot / "pages").mkdir(parents=True)
    (snapshot / "pages" / "help__late-fees.html").write_text(page("<p>A fee applies.</p>"))
    (snapshot / "pages" / "help__form.html").write_text(page("<form><input/></form>"))
    manifest = {
        "snapshot_id": "abc123",
        "sections": ["/help"],
        "pages": [
            {
                "url": "https://www.example.invalid/help/late-fees",
                "file": "pages/help__late-fees.html",
                "fetched_at": "2026-09-30T05:22:30+00:00",
                "lastmod": "2026-07-30T11:53:43+10:00",
            },
            {
                "url": "https://www.example.invalid/help/form",
                "file": "pages/help__form.html",
                "fetched_at": "2026-09-30T05:22:31+00:00",
                "lastmod": None,
            },
        ],
    }
    (snapshot / "manifest.json").write_text(json.dumps(manifest))

    summary = convert(snapshot, tmp_path / "out", log=lambda _: None)

    assert summary["empty"] == ["https://www.example.invalid/help/form"]
    body, meta = read_page(tmp_path / "out" / "help__late-fees.md")
    assert body == "# Late fees\n\nA fee applies."
    assert meta == {
        "title": "Late fees",
        "url": "https://www.example.invalid/help/late-fees",
        "section": "/help",
        "lastmod": "2026-07-30T11:53:43+10:00",
        "fetched_at": "2026-09-30T05:22:30+00:00",
        "snapshot": "abc123",
    }
