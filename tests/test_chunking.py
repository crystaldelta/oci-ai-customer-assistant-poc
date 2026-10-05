"""Tests for chunking pages by heading."""

from assistant import config
from assistant.chunking import MAX_CHARS, chunk_page
from assistant.ingest import read_page

PAGE = """# Late fees

A late fee may apply.

## Waivers

### Can it be waived?

Yes, once a year.

## How much is it?

It is shown on your bill.
"""


def test_each_section_is_a_chunk_starting_with_its_heading_path():
    chunks = chunk_page(PAGE, "Late fees")

    assert [c.text.split("\n\n")[0] for c in chunks] == [
        "Late fees",
        "Late fees > Waivers > Can it be waived?",
        "Late fees > How much is it?",
    ]
    assert chunks[1].text.endswith("Yes, once a year.")
    assert chunks[1].headings == ["Late fees", "Waivers", "Can it be waived?"]


def test_headings_with_nothing_under_them_but_subsections_are_not_chunks():
    texts = [c.text for c in chunk_page(PAGE, "Late fees")]

    assert not any(t.endswith("Waivers") for t in texts)


def test_a_long_section_is_split_and_every_piece_keeps_the_path():
    paragraphs = "\n\n".join(f"Paragraph {n}. " + "Some words about paying bills. " * 12 for n in range(12))
    chunks = chunk_page(f"## Paying your bill\n\n{paragraphs}", "Ways to pay")

    assert len(chunks) > 1
    for chunk in chunks:
        path, _, body = chunk.text.partition("\n\n")
        assert path == "Ways to pay > Paying your bill"
        assert len(body) <= MAX_CHARS


def test_every_fixture_page_chunks_with_its_title_first():
    for path in sorted(config.settings.content_dir.glob("*.md")):
        body, meta = read_page(path)
        chunks = chunk_page(body, meta["title"])
        assert chunks, path.name
        assert all(c.headings[0] == meta["title"] for c in chunks), path.name
        assert not any(c.text.startswith("# ") for c in chunks), path.name
