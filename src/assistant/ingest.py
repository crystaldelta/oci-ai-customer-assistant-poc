"""Ingest Markdown content into the Qdrant collection.

Reads every `*.md` file in CONTENT_DIR (`content/` by default: Markdown with
`title` and `url` front matter), chunks it by heading (`chunking.py`), embeds the
chunks and recreates the collection.

Each chunk's payload carries the page's `source` (title), `url` and `file`, and
its `headings`, plus any front matter field named in `PAGE_FIELDS`.

A chunk that repeats one already read is dropped (`drop_duplicates`): websites
repeat FAQ answers across pages, and help hubs show their top FAQs twice.
Repeats add no answers, only near-identical results that crowd out others.

Usage:
    python -m assistant.ingest               # always re-ingest
    python -m assistant.ingest --if-empty    # no-op if the collection has vectors

--if-empty is what the `ingest` Compose service runs, so `docker compose up`
only pays the embedding cost on a cold volume.

With QDRANT_PATH set, it writes an embedded index in that folder instead of
using the server. `publish.py` builds an embedded index the same way and
publishes it to INDEX_STORE.
"""

import argparse
import re
import time
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams

from assistant import config
from assistant.chunking import chunk_page
from assistant.retrieval import get_embeddings

# Front matter copied to each chunk when present: add a field a search filters on (AD-16).
PAGE_FIELDS = ("section", "lastmod")
# A body this long answers on its own, so a repeat under another heading is still a duplicate.
# A shorter one ("Yes, free of charge", "Ten years") means something only under its heading, so
# it is a duplicate only under the same heading.
STANDALONE_CHARS = 200
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def read_page(path: Path) -> tuple[str, dict]:
    """Return a page's body and metadata (`title`, `url` from its front matter)."""
    text = path.read_text(encoding="utf-8")
    meta: dict = {}
    match = _FRONT_MATTER.match(text)
    if match:
        for line in match.group(1).splitlines():
            key, sep, value = line.partition(":")
            if sep:
                meta[key.strip()] = value.strip()
        text = text[match.end() :]
    meta.setdefault("title", path.stem.replace("-", " ").capitalize())
    return text.strip(), meta


def page_chunks(path: Path) -> tuple[list[str], list[dict]]:
    """One page's chunk texts and their payloads."""
    body, meta = read_page(path)
    texts, metadatas = [], []
    for i, chunk in enumerate(chunk_page(body, meta["title"])):
        texts.append(chunk.text)
        metadatas.append(
            {
                "source": meta["title"],
                "url": meta.get("url"),
                "file": path.name,
                "chunk_index": i,
                "headings": chunk.headings,
                **{k: meta[k] for k in PAGE_FIELDS if k in meta},
            }
        )
    return texts, metadatas


def drop_duplicates(texts: list[str], metadatas: list[dict]) -> tuple[list[str], list[dict], int]:
    """Keep the first of each repeated chunk; return the kept texts and payloads and the number dropped.

    Two chunks repeat when their bodies (the text after the heading path, ignoring case and
    spacing) match, and either the body is at least STANDALONE_CHARS long or they sit under the
    same last heading.
    """
    seen: set[tuple] = set()
    kept_texts, kept_metadatas = [], []
    for text, meta in zip(texts, metadatas, strict=True):
        body = " ".join(text.partition("\n\n")[2].split()).casefold()
        heading = None if len(body) >= STANDALONE_CHARS else meta["headings"][-1].casefold()
        key = (body, heading)
        if key in seen:
            continue
        seen.add(key)
        kept_texts.append(text)
        kept_metadatas.append(meta)
    return kept_texts, kept_metadatas, len(texts) - len(kept_texts)


def wait_for_qdrant(timeout: int = 120) -> QdrantClient:
    """Compose starts this alongside Qdrant, so poll until it accepts connections.

    An embedded index (QDRANT_PATH) needs no server, so it is opened at once.
    """
    if config.settings.qdrant_path:
        config.settings.qdrant_path.mkdir(parents=True, exist_ok=True)
        return QdrantClient(path=str(config.settings.qdrant_path))
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            client = QdrantClient(url=config.settings.qdrant_url)
            client.get_collections()
            return client
        except Exception as exc:  # noqa: BLE001 — any transport error means "not up yet"
            last_error = exc
            time.sleep(2)
    raise SystemExit(f"Qdrant at {config.settings.qdrant_url} not reachable after {timeout}s: {last_error}")


def collection_count(client: QdrantClient) -> int:
    if not client.collection_exists(config.settings.collection_name):
        return 0
    return client.count(config.settings.collection_name).count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help="skip ingestion when the collection already contains vectors",
    )
    args = parser.parse_args()

    client = wait_for_qdrant()
    collection = config.settings.collection_name

    if args.if_empty:
        existing = collection_count(client)
        if existing:
            print(f"Collection '{collection}' already has {existing} vectors; skipping ingestion.")
            return

    content_dir = config.settings.content_dir
    if not any(content_dir.glob("*.md")):
        raise SystemExit(
            f"No Markdown files found in {content_dir}. Put the content there (README, 'Content'), "
            "or set CONTENT_DIR=content for the synthetic sample."
        )
    build(content_dir, client, collection)
    print(f"At {config.settings.qdrant_path or config.settings.qdrant_url}")
    client.close()  # an embedded index is written out on close


def build(content_dir: Path, client: QdrantClient, collection: str, embeddings=None) -> int:
    """Chunk and embed every page in `content_dir` into a new `collection`; return its size.

    `embeddings` is for tests; by default the configured fastembed model.
    """
    files = sorted(content_dir.glob("*.md"))
    print(f"Found {len(files)} pages in {content_dir}")

    texts, metadatas = [], []
    for path in files:
        page_texts, page_metadatas = page_chunks(path)
        texts += page_texts
        metadatas += page_metadatas
        print(f"  {path.name}: {len(page_texts)} chunks")

    texts, metadatas, dropped = drop_duplicates(texts, metadatas)
    if dropped:
        print(f"\nDropped {dropped} chunks that repeat another")

    print(f"\nEmbedding {len(texts)} chunks with {config.settings.embedding_model} (first run downloads the model)...")
    embeddings = embeddings or get_embeddings()
    dim = len(embeddings.embed_query("dimension probe"))

    if client.collection_exists(collection):
        print(f"Recreating existing collection '{collection}'")
        client.delete_collection(collection)
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )

    from langchain_qdrant import QdrantVectorStore

    store = QdrantVectorStore(client=client, collection_name=collection, embedding=embeddings)
    store.add_texts(texts, metadatas=metadatas)

    count = client.count(collection).count
    print(f"\nDone: {count} vectors in collection '{collection}' ({dim}d, cosine)")
    return count


if __name__ == "__main__":
    main()
