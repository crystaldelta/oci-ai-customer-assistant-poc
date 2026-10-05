"""The `retriever` seam: content passages for a query, chosen by `RETRIEVER`.

Qdrant with ONNX embeddings through fastembed, so no torch is needed. Queries
and passages are embedded differently (BGE models expect a query prefix);
fastembed applies that when `query_embed` is used. With `QDRANT_PATH` set,
Qdrant runs embedded on an index folder rather than a server. With
`INDEX_STORE` set, as on AgentCore, it runs embedded on the index
`publish.py` published, and picks up a newer one by itself (AD-22).

A managed search service (Bedrock Knowledge Base, Azure AI Search) would be
another `Retriever` here, chosen by a new `RETRIEVER` value.
"""

import logging
import shutil
import threading
from dataclasses import dataclass
from typing import Protocol

from langchain_core.embeddings import Embeddings

from assistant.config import Settings
from assistant.config import settings as _settings
from assistant.index_store import IndexSource, open_store

log = logging.getLogger("ai-starter")


@dataclass(frozen=True)
class Passage:
    """One retrieved chunk. `url` is None when the page has none, and `source` (the page title)
    is empty when it has no title.

    `score` is the search's similarity, comparable only within one embedding model; None when the
    provider gives none that the service may compare with `RETRIEVAL_MIN_SCORE`.
    """

    source: str
    content: str
    url: str | None = None
    score: float | None = None


class Retriever(Protocol):
    def search(self, query: str) -> list[Passage]:
        """The best passages for `query`, best first.

        A filter by user context (region, customer segment) is a keyword argument here, matched
        against a chunk payload field that `chunking.py` sets; see `docs/ARCHITECTURE.md`."""
        ...


class FastEmbedEmbeddings(Embeddings):
    """LangChain `Embeddings` over a fastembed `TextEmbedding` model."""

    def __init__(self, model_name: str | None = None, cache_dir: str | None = None):
        from fastembed import TextEmbedding

        self.model_name = model_name or _settings.embedding_model
        self._model = TextEmbedding(model_name=self.model_name, cache_dir=cache_dir or _settings.embedding_cache_dir)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.passage_embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.query_embed(text))).tolist()


def get_embeddings() -> FastEmbedEmbeddings:
    """First call downloads the model (about 200 MB) into the fastembed cache."""
    return FastEmbedEmbeddings()


def qdrant_location(settings: Settings) -> dict:
    """Where the collection is: the embedded index at `qdrant_path` if set, else the server."""
    return {"path": str(settings.qdrant_path)} if settings.qdrant_path else {"url": settings.qdrant_url}


def get_vector_store(embeddings: Embeddings | None = None):
    from langchain_qdrant import QdrantVectorStore

    return QdrantVectorStore.from_existing_collection(
        **qdrant_location(_settings),
        collection_name=_settings.collection_name,
        embedding=embeddings or get_embeddings(),
    )


def _passage(doc, score: float) -> Passage:
    return Passage(
        source=doc.metadata.get("source") or "",
        content=doc.page_content,
        url=doc.metadata.get("url"),
        score=score,
    )


_CANDIDATES = 4  # with a per-page cap, search this many times `top_k` to fill the places


def per_page(hits: list, top_k: int, max_per_page: int) -> list:
    """The first `top_k` of `hits` (Qdrant's `(doc, score)` pairs, best first), with at most
    `max_per_page` from any one page (by URL). 0 is no cap."""
    if not max_per_page:
        return hits[:top_k]
    kept, count = [], {}
    for doc, score in hits:
        page = doc.metadata.get("url") or doc.metadata.get("source")
        if count.get(page, 0) < max_per_page:
            kept.append((doc, score))
            count[page] = count.get(page, 0) + 1
            if len(kept) == top_k:
                break
    return kept


class QdrantRetriever:
    """Qdrant search. The embedding model loads on the first search, not at startup.

    At most `max_per_page` passages come from one page: pages repeat a question across sections
    and FAQ pages, and one page taking most places pushes out the page that answers (AD-15).

    With `source`, the index is the published one (AD-22): downloaded on the first search, and
    on a search once `source.check_seconds` have passed, a newer one is downloaded, checked and
    swapped in. If that fails, the current index stays in use and the next check tries again.
    The index it replaces is closed at the following swap, not at once, so a search still
    running on it finishes.

    `store` is for tests, which pass an in-memory Qdrant; `embeddings` too.
    """

    def __init__(
        self,
        top_k: int,
        max_per_page: int = 0,
        store=None,
        source: IndexSource | None = None,
        collection: str | None = None,
        embeddings: Embeddings | None = None,
    ) -> None:
        self.top_k = top_k
        self.max_per_page = max_per_page
        self._store = store
        self._source = source
        self._collection = collection or _settings.collection_name
        self._embeddings = embeddings
        self._retired: tuple | None = None  # (store, folder) replaced at the last swap
        self._folder = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._store is not None

    def _current(self):
        with self._lock:
            if self._source is not None:
                if self._source.due():
                    self._check_source()
            elif self._store is None:
                self._store = get_vector_store()
            return self._store

    def _check_source(self) -> None:
        """Swap in a newer published index, if there is one. Raises only with nothing to serve."""
        from langchain_qdrant import QdrantVectorStore

        try:
            found = self._source.changed()
            if found is None:
                return
            meta, folder = found
            if self._embeddings is None:
                self._embeddings = get_embeddings()
            store = QdrantVectorStore.from_existing_collection(
                path=str(folder), collection_name=self._collection, embedding=self._embeddings
            )
            if not store.client.count(self._collection).count:
                store.client.close()
                raise ValueError(f"index {meta['index']} has no points")
        except Exception:
            if self._store is None:
                raise
            current = self._source.current["index"] if self._source.current else None
            log.warning("index: kept %s; the newer index could not be loaded", current, exc_info=True)
            return
        self._source.accept()
        if self._retired is not None:
            old_store, old_folder = self._retired
            old_store.client.close()
            shutil.rmtree(old_folder, ignore_errors=True)
        self._retired = (self._store, self._folder) if self._store is not None else None
        self._store, self._folder = store, folder
        _published.update(index=meta["index"], content_id=meta.get("content_id"), points=meta.get("points"))
        log.info("index: loaded %s (%s points)", meta["index"], meta.get("points"))

    def search(self, query: str) -> list[Passage]:
        store = self._current()
        vector = store.embeddings.embed_query(query)
        k = self.top_k * _CANDIDATES if self.max_per_page else self.top_k
        found = store.similarity_search_with_score_by_vector(vector, k=k)
        return [_passage(doc, score) for doc, score in per_page(found, self.top_k, self.max_per_page)]


# The published index in use in this process, for health (AD-22).
_published: dict = {}


def get_retriever(settings: Settings) -> Retriever:
    if settings.retriever == "qdrant" and settings.index_store:
        source = IndexSource(
            open_store(settings.index_store, settings.aws_region),
            cache_dir=settings.index_cache_dir,
            embedding_model=settings.embedding_model,
            check_seconds=settings.index_check_seconds,
        )
        return QdrantRetriever(
            top_k=settings.top_k, max_per_page=settings.max_per_page, source=source, collection=settings.collection_name
        )
    if settings.retriever == "qdrant":
        return QdrantRetriever(top_k=settings.top_k, max_per_page=settings.max_per_page)
    raise NotImplementedError(f"RETRIEVER={settings.retriever} is not supported")


def health(settings: Settings) -> dict:
    """Reachability and size of the content store. Never raises."""
    status = {"retriever": settings.retriever}
    if settings.index_store:
        # The published index loads on the first search, so before that there is nothing to report.
        # The store's address stays out: /api/health is public.
        return status | {"collection": settings.collection_name, "index_store": True, **_published}
    if settings.qdrant_path:
        # An embedded index admits one client per process, and the retriever holds it, so health
        # only checks the folder is there.
        present = (settings.qdrant_path / "collection" / settings.collection_name).is_dir()
        status |= {"qdrant_path": str(settings.qdrant_path), "collection": settings.collection_name}
        status["index_present"] = present
        return status if present else status | {"error": f"qdrant: no index at {settings.qdrant_path}"}
    from qdrant_client import QdrantClient

    status |= {
        "qdrant_url": settings.qdrant_url,
        "collection": settings.collection_name,
        "embedding_model": settings.embedding_model,
        "qdrant_ok": False,
        "points": None,
    }
    try:
        info = QdrantClient(url=settings.qdrant_url, timeout=3).get_collection(settings.collection_name)
        status["qdrant_ok"] = True
        status["points"] = info.points_count
    except Exception:  # noqa: BLE001 — health must not raise
        # The detail goes to the log, not the response: /api/health is public.
        log.warning("health: qdrant unreachable", exc_info=True)
        status["error"] = "qdrant: unreachable"
    return status
