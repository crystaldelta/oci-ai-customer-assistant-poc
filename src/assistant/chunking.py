"""Split a Markdown page into chunks by heading (AD-16).

A chunk is one section: the text under a heading, down to the next heading.
Its text starts with the heading path ("Late fees > Can it be waived?"), so a
retrieved chunk makes sense on its own and the path is embedded with it. A
section longer than MAX_CHARS is split at paragraph breaks, and each piece keeps
the path. On a typical help site most sections are one FAQ question and its answer.

A filter by user context (a region, a customer segment) starts here: derive it
from the page's URL, title or headings, never from body text, and add it to the
chunk (see `docs/ARCHITECTURE.md`, AD-16).
"""

import re
from dataclasses import dataclass, field

from langchain_text_splitters import RecursiveCharacterTextSplitter

MAX_CHARS = 1000  # bge-base reads 512 tokens, about 2,000 characters; shorter means less model input
OVERLAP = 100

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass
class Chunk:
    text: str  # heading path, a blank line, then the section text
    headings: list[str]  # the path, page title first


@dataclass
class _Section:
    headings: list[str]
    lines: list[str] = field(default_factory=list)


def sections(markdown: str, title: str) -> list[_Section]:
    """The page's sections in order. Headings nest by level; the page's `#` is the title."""
    stack: list[tuple[int, str]] = []
    current = _Section([title])
    found = [current]
    for line in markdown.splitlines():
        if m := _HEADING.match(line):
            level, text = len(m.group(1)), m.group(2)
            if level == 1:
                continue  # the page title, already the root of every path
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
            current = _Section([title, *(h for _, h in stack)])
            found.append(current)
        else:
            current.lines.append(line)
    return found


_splitter = RecursiveCharacterTextSplitter(
    chunk_size=MAX_CHARS, chunk_overlap=OVERLAP, separators=["\n\n", "\n", ". ", " ", ""]
)


def chunk_page(markdown: str, title: str) -> list[Chunk]:
    """Chunks for one page body (without its front matter)."""
    chunks = []
    for section in sections(markdown, title):
        body = re.sub(r"\n{3,}", "\n\n", "\n".join(section.lines)).strip()
        if not body:
            continue  # a heading with only subsections under it
        path = " > ".join(section.headings)
        pieces = _splitter.split_text(body) if len(body) > MAX_CHARS else [body]
        chunks.extend(Chunk(f"{path}\n\n{piece}", section.headings) for piece in pieces)
    return chunks
