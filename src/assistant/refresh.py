"""The scheduled website content refresh (AD-24): sitemap refresh, clean, publish.

For a project whose content is the client's website, run on a schedule as a container task (the
Dockerfile's `refresh` target) against the index store `INDEX_STORE` names. Each run:

1. Downloads the published raw snapshot, and refreshes it from the site with as few requests as
   it can (`snapshot.refresh`: `robots.txt`, the sitemap, and only the pages whose sitemap
   `lastmod` changed). An unchanged site gives the same snapshot ID, and the run ends there.
2. Cleans the new snapshot (`clean.convert`) into a temporary folder.
3. Publishes it through `publish.run`: builds the index, checks it against the published one
   (pages and points at least 90% of it, the same embedding model), and uploads the snapshot,
   the content, the index, then `LATEST.json`, which the agent watches.

The first run has no published snapshot to refresh: it either seeds one from a local snapshot
(`--seed`, no requests) or crawls the sections (`--crawl`, `snapshot.crawl`, with the usual
waits). A crawl can also be run later, by hand, to pick up pages the sitemap does not show; it
is never scheduled.

Exit codes: 0 published or nothing changed; 1 refused by the site (the response is saved to
`refused/` in the store for review: never retry around it); 2 not published (no baseline, no
sitemap, or a failed check).

Usage:
    python -m assistant.refresh                             # the scheduled run
    python -m assistant.refresh --force                     # rebuild even if unchanged, publish past the checks
    python -m assistant.refresh --crawl                     # first publish: crawl the sections
    python -m assistant.refresh --seed data/snapshots/<id>  # first publish from a local snapshot; no requests
"""

import argparse
import tempfile
from collections.abc import Callable
from pathlib import Path

import httpx

from assistant import clean, config, publish, snapshot
from assistant.index_store import IndexStore, open_store

PUBLISHED, REFUSED, NOT_PUBLISHED = 0, 1, 2


def run(
    store: IndexStore,
    work: Path,
    *,
    seed: Path | None = None,
    crawl: bool = False,
    force: bool = False,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] | None = None,
    embeddings=None,
    log: Callable[[str], None] = print,
) -> int:
    """One refresh in the folder `work`. `client`, `sleep` and `embeddings` are for tests."""
    published = store.latest()
    old = published[0] if published else None

    if seed:
        snapshot_dir = seed
        log(f"Seeding from {seed}; no requests to the site")
    else:
        if not crawl and not (old and old.get("snapshot_id")):
            log("No website snapshot is published yet: run with --crawl (or --seed) first.")
            return NOT_PUBLISHED
        site, sections = snapshot.site_url(), snapshot.sections()
        snapshot_dir = work / "snapshot"
        own_client = client is None
        if own_client:
            client = httpx.Client(headers=snapshot.HEADERS, timeout=snapshot.TIMEOUT)
        fetcher = snapshot.new_fetcher(client, **({"sleep": sleep} if sleep else {}))
        try:
            if crawl:
                log(f"Crawling {site}, sections {', '.join(sections)}")
                manifest = snapshot.crawl(fetcher, snapshot_dir, site=site, sections=sections, log=log)
            else:
                previous = store.download_snapshot(old["snapshot_id"], work / "previous")
                manifest = snapshot.refresh(fetcher, previous, snapshot_dir, site=site, sections=sections, log=log)
        except snapshot.Blocked as exc:
            key = store.save_refusal(snapshot.refusal_record(exc.response))
            log(f"Stopped: the site refused the crawler ({exc}). The response is in {key}; review it.")
            log("If it is bot protection, ask the client to allow the crawler; do not retry around it.")
            return REFUSED
        except snapshot.NoSitemap as exc:
            log(f"Stopped: {exc}. The published index stays.")
            return NOT_PUBLISHED
        finally:
            if own_client:
                client.close()
        if crawl:
            log(
                f"Crawl: {len(manifest['pages'])} pages saved, {len(manifest['not_saved'])} not saved, "
                f"{manifest['requests']} requests"
            )
        else:
            log(
                f"Refresh: {manifest['fetched']} pages fetched, {manifest['carried']} copied, "
                f"{len(manifest['removed'])} removed, {manifest['requests']} requests"
            )
        if old and manifest["snapshot_id"] == old.get("snapshot_id") and not force:
            log(f"No change: snapshot {old['snapshot_id']} is still current.")
            return PUBLISHED

    clean.convert(snapshot_dir, work / "content", log=log)
    return publish.run(
        store, work / "content", work, force=force, snapshot_dir=snapshot_dir, embeddings=embeddings, log=log
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    first = parser.add_mutually_exclusive_group()
    first.add_argument("--crawl", action="store_true", help="crawl the sections rather than refresh from the sitemap")
    first.add_argument("--seed", type=Path, metavar="SNAPSHOT_DIR", help="publish this local snapshot; no requests")
    parser.add_argument(
        "--force", action="store_true", help="rebuild even if nothing changed, and publish past a failed check"
    )
    args = parser.parse_args()
    if not config.settings.index_store:
        parser.error("INDEX_STORE is not set (s3://bucket/prefix, or a local folder)")
    if args.seed and not (args.seed / "manifest.json").exists():
        parser.error(f"no manifest.json in {args.seed}")
    store = open_store(config.settings.index_store, config.settings.aws_region)
    with tempfile.TemporaryDirectory(prefix="assistant-refresh-") as work:
        return run(store, Path(work), seed=args.seed, crawl=args.crawl, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
