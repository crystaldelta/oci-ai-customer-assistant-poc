"""Tests for reading the fixture corpus. No Qdrant, no embedding model."""

from assistant import config
from assistant.ingest import drop_duplicates, page_chunks, read_page


def test_every_fixture_page_has_a_title_and_url_and_is_marked_synthetic():
    pages = sorted(config.settings.content_dir.glob("*.md"))
    assert 5 <= len(pages) <= 10

    for path in pages:
        body, meta = read_page(path)
        assert meta["title"], path.name
        assert meta["url"].startswith("https://example.invalid/"), path.name
        assert meta["synthetic"] == "true", path.name
        assert "Synthetic sample content" in meta["note"], path.name
        assert body.startswith("# "), path.name


def test_read_page_strips_front_matter(tmp_path):
    page = tmp_path / "sample-page.md"
    page.write_text("---\ntitle: Sample\nurl: https://example.invalid/x\n---\n\n# Body\n")

    body, meta = read_page(page)

    assert body == "# Body"
    assert meta == {"title": "Sample", "url": "https://example.invalid/x"}


def test_read_page_falls_back_to_the_file_name_for_a_title(tmp_path):
    page = tmp_path / "sample-page.md"
    page.write_text("# Body\n")

    assert read_page(page)[1]["title"] == "Sample page"


def test_page_chunks_carry_the_page_and_chunk_metadata(tmp_path):
    page = tmp_path / "late-fees.md"
    page.write_text(
        "---\ntitle: Late fees\nurl: https://example.invalid/late-fees\nsection: /help\n"
        "lastmod: 2026-07-01\nfetched_at: 2026-09-30T05:00:00+00:00\n---\n\n"
        "# Late fees\n\nA fee may apply.\n\n## Waivers\n\nOnce a year.\n"
    )

    texts, metadatas = page_chunks(page)

    assert texts == ["Late fees\n\nA fee may apply.", "Late fees > Waivers\n\nOnce a year."]
    assert metadatas[1] == {
        "source": "Late fees",
        "url": "https://example.invalid/late-fees",
        "file": "late-fees.md",
        "chunk_index": 1,
        "headings": ["Late fees", "Waivers"],
        "section": "/help",
        "lastmod": "2026-07-01",
    }


def test_fixture_chunks_have_only_the_page_fields():
    texts, metadatas = page_chunks(config.settings.content_dir / "ways-to-pay.md")

    assert texts and set(metadatas[0]) == {"source", "url", "file", "chunk_index", "headings"}


def test_the_sample_content_and_the_test_fixtures_are_the_same_pages():
    sample = config.ROOT / "content"

    assert sorted(p.name for p in sample.glob("*.md")) == sorted(
        p.name for p in config.settings.content_dir.glob("*.md")
    )


ANSWER = "You can pay by BPAY, direct debit or credit card, or in person at a post office. " * 3


def _chunk(path: str, body: str) -> tuple[str, dict]:
    headings = path.split(" > ")
    return f"{path}\n\n{body}", {"headings": headings}


def _dedupe(*chunks):
    texts, metadatas = zip(*chunks, strict=True)
    return drop_duplicates(list(texts), list(metadatas))


def test_a_repeated_answer_is_kept_once_whatever_its_heading():
    texts, _, dropped = _dedupe(
        _chunk("Help and support > Top FAQs > How can I pay my bill?", ANSWER),
        _chunk("Payment options > Ways to pay", "  " + ANSWER.upper()),
    )

    assert dropped == 1
    assert texts == [f"Help and support > Top FAQs > How can I pay my bill?\n\n{ANSWER}"]


def test_a_short_body_repeats_only_under_the_same_heading():
    texts, _, dropped = _dedupe(
        _chunk("Compare devices > Model A", "Warranty: ten years."),
        _chunk("Compare devices > Model B", "Warranty: ten years."),
        _chunk("Help > Tabs > Live chat", "Chat with us."),
        _chunk("Help > Tabs (mobile) > Live chat", "Chat with us."),
    )

    assert dropped == 1
    assert [t.split("\n\n")[0] for t in texts] == [
        "Compare devices > Model A",
        "Compare devices > Model B",
        "Help > Tabs > Live chat",
    ]
