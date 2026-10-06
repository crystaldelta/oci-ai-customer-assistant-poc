"""Turn a site snapshot's raw HTML into clean Markdown pages (AD-24).

Reads a snapshot from SNAPSHOT_DIR (the one named in LATEST by default) and writes one Markdown
file per page to SITE_CONTENT_DIR/<snapshot id>/, with front matter that `ingest.read_page`
understands (title, url, section, lastmod, fetched_at, snapshot). SITE_CONTENT_DIR/LATEST then
names that snapshot, so `CONTENT_DIR=data/content` ingests it.

What is kept and what is dropped:

- Only the page's main content: the first selector in `SITE_CONTENT_SELECTOR` that matches
  (`main, [role=main], article` by default; `scripts/survey_site.py` suggests one for the site),
  or the whole body without its header. Navigation, footers and asides are dropped wherever
  they are.
- Collapsed content is kept although the site may mark it `hidden`: that is how most FAQ
  answers are shown. A `<details>` element's `<summary>` becomes a heading.
- Tables become Markdown tables; lists keep their nesting and numbering.
- Forms, embedded frames, images, scripts, screen-reader-only text and bare "Find out more"
  buttons are dropped. Links keep their text; a phone or email link whose text does not show
  the number or address gets it added.

A site with its own structures (accordions built from `div`s, tick icons in tables, blocks shown
only in some regions) needs a few lines here: see docs/website-content.md, "Adapting the cleaner".

Usage:
    python -m assistant.clean                    # the LATEST snapshot
    python -m assistant.clean --snapshot <id>
"""

import argparse
import json
import re
import shutil
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from assistant import config

_SKIP_TAGS = {
    "script", "style", "noscript", "template", "svg", "img", "picture", "source", "video", "audio",
    "iframe", "form", "input", "select", "textarea", "button", "label", "nav", "footer", "aside",
}  # fmt: skip
_SKIP_CLASSES = {"visually-hidden", "sr-only", "screen-reader-text", "skip-link", "skip-to-content"}
_INLINE_TAGS = {
    "a", "abbr", "b", "br", "cite", "code", "em", "font", "i", "mark", "q", "s", "small", "span",
    "strong", "sub", "sup", "time", "u",
}  # fmt: skip
# Button and link-card text that means nothing on its own, dropped when it is a whole paragraph.
_CALLS_TO_ACTION = {
    "find out more", "learn more", "read more", "more info", "click here", "get started", "live chat",
    "chat with us", "send message", "get in touch", "log in", "register", "sign up", "sign up now",
    "apply now", "contact us", "back to top", "share", "skip to main content", "skip to content",
}  # fmt: skip
_HEADING = re.compile(r"^h[1-6]$")
_TITLE_SUFFIX = re.compile(r"\s+[|–—-]\s+[^|–—-]+$")  # " | Site name" after the page's own title


def _space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _skipped(el: Tag) -> bool:
    return el.name in _SKIP_TAGS or bool(_SKIP_CLASSES & set(el.get("class", [])))


def _with_contact(text: str, href: str) -> str:
    """A phone or email link's text, with the number or address added if it does not show it."""
    for scheme in ("tel:", "mailto:"):
        if href.startswith(scheme):
            target = href.removeprefix(scheme).strip()
            if target.replace(" ", "") not in text.replace(" ", ""):
                return f"{text} ({target})" if _space(text) else target
    return text


class _Renderer:
    """HTML to Markdown blocks, for the structures most content sites use."""

    def __init__(self, title: str):
        self.title = title
        self.level = 1  # the last heading level written; the page title is level 1

    # ---------------------------------------------------------------- inline

    def inline(self, el: Tag | NavigableString) -> str:
        if isinstance(el, NavigableString):
            return str(el) if type(el) is NavigableString else ""  # not comments or CDATA
        if _skipped(el):
            return ""
        if el.name == "br":
            return " "
        text = "".join(self.inline(c) for c in el.children)
        return _with_contact(text, el.get("href", "")) if el.name == "a" else text

    # ---------------------------------------------------------------- blocks

    def blocks(self, el: Tag, keep_all: bool = False) -> list[str]:
        """Blocks of `el`. A paragraph that is only a button label is dropped, unless `keep_all`
        (a list item or table cell, where "Log in" can be a step)."""
        out: list[str] = []
        run: list[str] = []  # inline content waiting to become a paragraph

        def flush():
            text = _space("".join(run))
            if text and (keep_all or text.rstrip(".").casefold() not in _CALLS_TO_ACTION):
                out.append(text)
            run.clear()

        for child in el.children:
            if isinstance(child, NavigableString) or child.name in _INLINE_TAGS and not self._has_blocks(child):
                run.append(self.inline(child))
                continue
            flush()
            out.extend(self.block(child))
        flush()
        return out

    def _has_blocks(self, el: Tag) -> bool:
        return any(isinstance(c, Tag) and c.name not in _INLINE_TAGS for c in el.descendants)

    def block(self, el: Tag) -> list[str]:
        if _skipped(el):
            return []
        if _HEADING.match(el.name):
            return self.heading(el)
        if el.name == "details":
            return self.details(el)
        if el.name in ("ul", "ol"):
            return ["\n".join(self.list_items(el, 0))]
        if el.name == "table":
            return self.table(el)
        return self.blocks(el)

    def heading(self, el: Tag, text: str | None = None, level: int | None = None) -> list[str]:
        text = _space(text if text is not None else self.inline(el))
        if not text or (level is None and text.casefold() == self.title.casefold()):
            return []  # the page title, repeated in a banner
        level = level or max(2, int(el.name[1]))
        self.level = min(level, 6)
        return [f"{'#' * self.level} {text}"]

    def details(self, el: Tag) -> list[str]:
        """A collapsible item: its summary is a heading one level below the one it sits under."""
        base = self.level
        summary = el.find("summary", recursive=False)
        out = self.heading(el, self.inline(summary) if summary else "", level=min(base + 1, 6))
        if summary is not None:
            summary.extract()
        out.extend(self.blocks(el))
        self.level = base
        return out

    def list_items(self, el: Tag, depth: int) -> list[str]:
        lines = []
        for n, li in enumerate(el.find_all("li", recursive=False), 1):
            marker = f"{n}." if el.name == "ol" else "-"
            nested = li.find_all(["ul", "ol"], recursive=False)
            for sub in nested:
                sub.extract()
            text = " ".join(self.blocks(li, keep_all=True))
            if text:
                lines.append(f"{'  ' * depth}{marker} {text}")
            for sub in nested:
                lines.extend(self.list_items(sub, depth + 1))
        return lines

    def table(self, el: Tag) -> list[str]:
        rows = []
        for tr in el.find_all("tr"):
            if tr.find_parent("table") is not el:
                continue
            cells = []
            for cell in tr.find_all(["th", "td"], recursive=False):
                parts = [
                    re.sub(r"^\s*(?:-|\d+\.)\s+", "", line)
                    for b in self.blocks(cell, keep_all=True)
                    for line in b.splitlines()
                ]
                cells.append("; ".join(p for p in parts if p).replace("|", "\\|"))
                cells.extend([""] * (int(cell.get("colspan", 1) or 1) - 1))
            if any(cells):
                rows.append(cells)
        if not rows:
            return []
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        lines = [f"| {' | '.join(rows[0])} |", f"|{' --- |' * width}"]
        lines += [f"| {' | '.join(r)} |" for r in rows[1:]]
        return ["\n".join(lines)]


def page_title(soup: BeautifulSoup) -> str:
    """The page's visible heading, or its `<title>` without the site name."""
    h1 = soup.find("h1")
    if h1 and _space(h1.get_text(" ")):
        return _space(h1.get_text(" "))
    return _TITLE_SUFFIX.sub("", _space(soup.title.get_text())) if soup.title else ""


def main_content(soup: BeautifulSoup, selector: str) -> Tag | None:
    """The first selector in the comma-separated `selector` that matches, or the body without
    its header."""
    for part in (p.strip() for p in selector.split(",")):
        if part and (found := soup.select_one(part)):
            return found
    body = soup.body
    if body is not None:
        for header in body.find_all("header"):
            if header.find_parent(["article", "main", "section"]) is None:
                header.decompose()  # the site's header, not an article's
    return body


def clean_page(html: str, selector: str | None = None) -> tuple[str, str]:
    """Return a page's title and its main content as Markdown (empty if it has none)."""
    soup = BeautifulSoup(html, "html.parser")
    title = page_title(soup)
    body = main_content(soup, config.settings.site_content_selector if selector is None else selector)
    if body is None:
        return title, ""
    return title, "\n\n".join(_Renderer(title).blocks(body))


def front_matter(meta: dict) -> str:
    lines = [f"{k}: {v}" for k, v in meta.items() if v is not None]
    return "---\n" + "\n".join(lines) + "\n---\n"


def section_of(url: str, sections: list[str]) -> str | None:
    """The section a page was crawled under: a front matter field ingest copies to each chunk,
    for a filter by audience or segment (AD-16)."""
    path = "/" + url.split("://", 1)[-1].partition("/")[2]
    return next((s for s in sections if s != "/" and (path == s or path.startswith(s + "/"))), None)


def convert(snapshot_dir: Path, out_dir: Path, log=print) -> dict:
    """Clean every page of a snapshot into `out_dir`; return a summary."""
    manifest = json.loads((snapshot_dir / "manifest.json").read_text())
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    written, empty = [], []
    for page in manifest["pages"]:
        title, body = clean_page((snapshot_dir / page["file"]).read_text(encoding="utf-8"))
        if not body.strip():
            empty.append(page["url"])
            log(f"  empty  {page['url']}")
            continue
        meta = {
            "title": title,
            "url": page["url"],
            "section": section_of(page["url"], manifest["sections"]),
            "lastmod": page.get("lastmod"),
            "fetched_at": page["fetched_at"],
            "snapshot": manifest["snapshot_id"],
        }
        name = Path(page["file"]).with_suffix(".md").name
        (out_dir / name).write_text(f"{front_matter(meta)}\n# {title}\n\n{body}\n", encoding="utf-8")
        written.append({"url": page["url"], "file": name, "chars": len(body)})
    summary = {"snapshot": manifest["snapshot_id"], "pages": written, "empty": empty}
    (out_dir / "clean.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot", help="snapshot ID (default: the one named in LATEST)")
    args = parser.parse_args()

    root = config.settings.snapshot_dir
    snapshot = args.snapshot or (root / "LATEST").read_text().strip()
    out_dir = config.settings.site_content_dir / snapshot
    summary = convert(root / snapshot, out_dir)
    (config.settings.site_content_dir / "LATEST").write_text(snapshot + "\n")  # what ingest reads
    chars = sum(p["chars"] for p in summary["pages"])
    print(
        f"\nSnapshot {snapshot}: {len(summary['pages'])} pages cleaned ({chars:,} characters), "
        f"{len(summary['empty'])} with no content. In {out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
