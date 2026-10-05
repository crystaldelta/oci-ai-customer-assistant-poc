"""Build an embedded index from CONTENT_DIR and publish it to INDEX_STORE (AD-22).

The agent in the cloud has no Qdrant server: it downloads the index `LATEST.json` names on its
first search and swaps in a newer one by itself (`retrieval.QdrantRetriever`), so a content
change needs no redeploy. Each run:

1. Reads the Markdown in CONTENT_DIR (`title` and `url` front matter, as `ingest.py` reads it).
   Its content ID is a hash of the file names and contents, so unchanged content gives the same
   ID and the run ends there (unless `--force`).
2. Builds an embedded index from it in a temporary folder (`ingest.build`).
3. Checks the new index against the published one: pages and points at least `MIN_SHARE` of
   it, the same embedding model. A content folder gone thin should not empty the assistant.
4. Publishes: the content, the index, then `LATEST.json`, which the agent watches.

Getting the content into CONTENT_DIR is the project's job: copied from the client, exported from
their CMS, or crawled and cleaned (README, "Content").

Exit codes: 0 published or nothing changed; 2 not published (no content, or a failed check).

Usage:
    python -m assistant.publish                 # INDEX_STORE and CONTENT_DIR from the settings
    python -m assistant.publish --force         # rebuild even if unchanged, publish past the checks
    INDEX_STORE=data/store python -m assistant.publish   # a local store, for the agentcore profile
"""

import argparse
import hashlib
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from qdrant_client import QdrantClient

from assistant import config, ingest
from assistant.index_store import IndexStore, open_store

# A new index with fewer pages or points than this share of the published one is held back for
# a person to look at: a broken export or a half-copied folder should not empty the assistant.
MIN_SHARE = 0.9

PUBLISHED, NOT_PUBLISHED = 0, 2


def content_id(content_dir: Path) -> str:
    """A short hash of the Markdown files' names and contents: the same content, the same ID."""
    digest = hashlib.sha256()
    for path in sorted(content_dir.glob("*.md")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def problems(new: dict, old: dict | None) -> list[str]:
    """Why `new` should not replace `old` (both `LATEST.json` fields); empty if it may."""
    found = []
    if not new["points"]:
        found.append("the new index is empty")
    if old is None:
        return found
    if new["embedding_model"] != old["embedding_model"]:
        found.append(f"embedding model {new['embedding_model']} differs from the published {old['embedding_model']}")
    for field in ("pages", "points"):
        if new[field] < MIN_SHARE * old.get(field, 0):
            found.append(f"{field} fell from {old[field]} to {new[field]}")
    return found


def run(
    store: IndexStore,
    content_dir: Path,
    work: Path,
    *,
    force: bool = False,
    embeddings=None,
    log: Callable[[str], None] = print,
) -> int:
    """One publish, building in the folder `work`. `embeddings` is for tests."""
    pages = sorted(content_dir.glob("*.md"))
    if not pages:
        log(f"Not published: no Markdown files in {content_dir}.")
        return NOT_PUBLISHED
    published = store.latest()
    old = published[0] if published else None
    cid = content_id(content_dir)
    if old and old.get("content_id") == cid and not force:
        log(f"No change: content {cid} is already published as {old['index']}.")
        return PUBLISHED

    index_dir = work / "index"
    qdrant = QdrantClient(path=str(index_dir))
    try:
        points = ingest.build(content_dir, qdrant, config.settings.collection_name, embeddings=embeddings)
    finally:
        qdrant.close()  # an embedded index is written out on close

    new = {
        "index": f"{cid}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}",
        "content_id": cid,
        "embedding_model": config.settings.embedding_model,
        "collection": config.settings.collection_name,
        "points": points,
        "pages": len(pages),
    }
    found = problems(new, old)
    if found and not force:
        log("Not published: " + "; ".join(found) + ". Check the content, then publish with --force if it is right.")
        return NOT_PUBLISHED
    if found:
        log("Publishing anyway (--force): " + "; ".join(found))
    store.publish(content_dir, index_dir, new)
    log(f"Published index {new['index']}: {new['pages']} pages, {new['points']} points")
    return PUBLISHED


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--force", action="store_true", help="rebuild even if nothing changed, and publish past a failed check"
    )
    args = parser.parse_args()
    if not config.settings.index_store:
        parser.error("INDEX_STORE is not set (s3://bucket/prefix, or a local folder)")
    store = open_store(config.settings.index_store, config.settings.aws_region)
    with tempfile.TemporaryDirectory(prefix="assistant-publish-") as work:
        return run(store, config.settings.content_dir, Path(work), force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
