"""Retrieval check on the content, through the `retriever` seam (`RETRIEVER`, Qdrant by
default): proves the content store, the embeddings and the collection work, that sample
customer questions find the right page, and that `RETRIEVAL_MIN_SCORE` sits between the
in-scope and the off-topic questions.

The expectations are in `evals/retrieval.yaml` (or `--cases FILE`):

    first:      the page (any of `pages`, by URL path) must come first
    reached:    the page must be among the passages the model sees
    off_topic:  questions whose best passage must score below RETRIEVAL_MIN_SCORE

Every `first` and `reached` question must score at or above the floor. No model call, so Ollama
does not need to be running. It needs the content ingested into `QDRANT_COLLECTION` (the
Compose `ingest` service does it). Re-run it after any retrieval or content change, and use it
to set `RETRIEVAL_MIN_SCORE` and `RETRIEVAL_MAX_GAP` on the client's content.

Usage (from the project root):
    python scripts/verify_retrieval.py
    python scripts/verify_retrieval.py --cases evals/retrieval.yaml
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assistant.config import settings  # noqa: E402
from assistant.retrieval import Passage, get_retriever  # noqa: E402

CASES = ROOT / "evals" / "retrieval.yaml"


@dataclass(frozen=True)
class Case:
    """A question and the page that answers it (any of `pages`, by URL path)."""

    question: str
    pages: tuple[str, ...]


def load(path: Path) -> tuple[list[Case], list[Case], list[str]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    unknown = set(raw) - {"first", "reached", "off_topic"}
    if unknown:
        raise SystemExit(f"{path}: unknown sections {sorted(unknown)}")

    def cases(name: str) -> list[Case]:
        return [Case(c["question"], tuple(c["pages"])) for c in raw.get(name) or []]

    return cases("first"), cases("reached"), list(raw.get("off_topic") or [])


def best_score(hits: list[Passage]) -> float:
    return max((p.score for p in hits if p.score is not None), default=0.0)


def path(passage: Passage) -> str:
    return urlparse(passage.url).path if passage.url else "-"


def check(retriever, case: Case, reached: bool = False) -> bool:
    """Search, print the result and return whether it passed."""
    hits = retriever.search(case.question)
    ok = bool(hits) and (any(path(p) in case.pages for p in hits) if reached else path(hits[0]) in case.pages)
    print(f"\n{'OK  ' if ok else 'FAIL'} {case.question}\n     expected: {' or '.join(case.pages)}")
    for passage in hits:
        print(f"     {path(passage)}  {passage.score:.3f}" if passage.score is not None else f"     {path(passage)}")
    return ok


def check_scope(retriever, question: str, in_scope: bool) -> bool:
    score = best_score(retriever.search(question))
    ok = (score >= settings.min_score) == in_scope
    side = "at or above" if in_scope else "below"
    print(f"{'OK  ' if ok else 'FAIL'} {score:.3f} {side} {settings.min_score}: {question}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=Path, default=CASES, help="the expectations file")
    args = parser.parse_args()
    first, reached, off_topic = load(args.cases)

    retriever = get_retriever(settings)
    print(f"Collection {settings.collection_name}, top {settings.top_k}, {settings.max_per_page or 'any'} per page.")
    print("\n== Page first")
    failures = sum(not check(retriever, case) for case in first)
    print("\n== Page among the passages")
    failures += sum(not check(retriever, case, reached=True) for case in reached)
    print(f"\n== Best score against RETRIEVAL_MIN_SCORE ({settings.min_score})")
    questions = list(dict.fromkeys(case.question for case in [*first, *reached]))
    failures += sum(not check_scope(retriever, q, in_scope=True) for q in questions)
    failures += sum(not check_scope(retriever, q, in_scope=False) for q in off_topic)
    total = len(first) + len(reached) + len(questions) + len(off_topic)
    print(f"\n{total - failures}/{total} checks passed.")
    if total and failures == total:
        print(f"Nothing matched: is {settings.collection_name} filled from the content {args.cases.name} expects?")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
