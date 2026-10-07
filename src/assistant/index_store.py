"""Where published content lives in the cloud (AD-22): the content and its indexes, in S3.

`publish.py` (and `refresh.py`, for website content) publishes here, and the agent loads the
newest index from here (`IndexSource`, used by `retrieval.QdrantRetriever`). `INDEX_STORE` names
it: `s3://bucket/prefix`, or a local folder for tests and local runs. The layout under it, with
no archives:

    content/<content id>/*.md        the Markdown the index was built from, for review and rebuilds
    snapshots/<snapshot id>/...      website content only: the raw snapshot the next refresh
                                     starts from (manifest.json, sitemap.json, pages/*.html)
    indexes/<index>/...              an embedded Qdrant folder
    refused/<UTC time>.txt           a refresh the site refused, for review
    LATEST.json                      what to load (below)

`LATEST.json` is written last, so a reader never sees a half-published index. It lists the
index's files, so the agent needs only `s3:GetObject`:

    {"index", "content_id", "snapshot_id" (website content), "embedding_model", "collection",
     "points", "pages", "files", "published_at"}

On Azure the same layout fits a Blob Storage container: a third backend beside `LocalStore`
and `S3Store`.
"""

import hashlib
import json
import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

LATEST = "LATEST.json"


class IndexStore:
    """Publishing and loading on top of five primitives that each backend provides."""

    def _read(self, key: str) -> tuple[bytes, str] | None:
        """The object's bytes and ETag, or None if there is none."""
        raise NotImplementedError

    def _etag(self, key: str) -> str | None:
        raise NotImplementedError

    def _write(self, key: str, data: bytes) -> None:
        raise NotImplementedError

    def _upload(self, key: str, path: Path) -> None:
        raise NotImplementedError

    def _download(self, key: str, path: Path) -> None:
        raise NotImplementedError

    # ---- reading

    def latest(self) -> tuple[dict, str] | None:
        """The published `LATEST.json` and its ETag, or None before the first publish."""
        found = self._read(LATEST)
        return (json.loads(found[0]), found[1]) if found else None

    def etag(self) -> str | None:
        """`LATEST.json`'s ETag, without reading it: one small request."""
        return self._etag(LATEST)

    def download_index(self, meta: dict, dest: Path) -> Path:
        for name in meta["files"]:
            self._download(f"indexes/{meta['index']}/{name}", dest / name)
        return dest

    def download_snapshot(self, snapshot_id: str, dest: Path) -> Path:
        """Fetch a raw snapshot: its manifest, sitemap and the pages the manifest names."""
        prefix = f"snapshots/{snapshot_id}"
        dest.mkdir(parents=True, exist_ok=True)
        self._download(f"{prefix}/manifest.json", dest / "manifest.json")
        manifest = json.loads((dest / "manifest.json").read_text())
        self._download(f"{prefix}/sitemap.json", dest / "sitemap.json")
        for page in manifest["pages"]:
            self._download(f"{prefix}/{page['file']}", dest / page["file"])
        return dest

    # ---- publishing

    def upload_content(self, content_dir: Path, content_id: str) -> None:
        for path in sorted(content_dir.glob("*.md")):
            self._upload(f"content/{content_id}/{path.name}", path)

    def upload_snapshot(self, snapshot_dir: Path) -> str:
        manifest = json.loads((snapshot_dir / "manifest.json").read_text())
        prefix = f"snapshots/{manifest['snapshot_id']}"
        for name in ["sitemap.json", *(p["file"] for p in manifest["pages"])]:
            self._upload(f"{prefix}/{name}", snapshot_dir / name)
        self._upload(f"{prefix}/manifest.json", snapshot_dir / "manifest.json")  # last: it names the rest
        return manifest["snapshot_id"]

    def publish(self, content_dir: Path, index_dir: Path, meta: dict, snapshot_dir: Path | None = None) -> dict:
        """Upload the snapshot (website content), the content, then the index, then `LATEST.json`
        naming them. Returns `LATEST.json`."""
        if snapshot_dir is not None:
            self.upload_snapshot(snapshot_dir)
        self.upload_content(content_dir, meta["content_id"])
        files = sorted(str(p.relative_to(index_dir)) for p in index_dir.rglob("*") if p.is_file())
        files = [f for f in files if not f.endswith(".lock")]  # Qdrant's lock file, from the writer
        for name in files:
            self._upload(f"indexes/{meta['index']}/{name}", index_dir / name)
        latest = {**meta, "files": files, "published_at": _now()}
        self._write(LATEST, (json.dumps(latest, indent=2) + "\n").encode())
        return latest

    def save_refusal(self, text: str) -> str:
        key = f"refused/{datetime.now(UTC):%Y%m%dT%H%M%SZ}.txt"
        self._write(key, text.encode())
        return key


class LocalStore(IndexStore):
    """A folder with the S3 layout. The ETag is the content's hash."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _read(self, key):
        path = self.root / key
        if not path.is_file():
            return None
        data = path.read_bytes()
        return data, hashlib.sha256(data).hexdigest()

    def _etag(self, key):
        found = self._read(key)
        return found[1] if found else None

    def _write(self, key, data):
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _upload(self, key, path):
        self._write(key, path.read_bytes())

    def _download(self, key, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.root / key, path)


class S3Store(IndexStore):
    """A bucket and prefix. `client` is for tests, which pass a stub; otherwise the boto3 client
    is made on first use, with credentials from the usual AWS chain."""

    def __init__(self, bucket: str, prefix: str, region: str, client=None) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.region = region
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def _read(self, key):
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
        except Exception as exc:
            if _missing(exc):
                return None
            raise
        return response["Body"].read(), response["ETag"]

    def _etag(self, key):
        try:
            return self.client.head_object(Bucket=self.bucket, Key=self._key(key))["ETag"]
        except Exception as exc:
            if _missing(exc):
                return None
            raise

    def _write(self, key, data):
        self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data)

    def _upload(self, key, path):
        self.client.upload_file(str(path), self.bucket, self._key(key))

    def _download(self, key, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, self._key(key), str(path))


def _missing(exc: Exception) -> bool:
    """True for S3's "no such key" (botocore's ClientError carries the code)."""
    code = getattr(exc, "response", {}).get("Error", {}).get("Code")
    return code in {"404", "NoSuchKey", "NotFound"}


def open_store(uri: str, region: str) -> IndexStore:
    """`s3://bucket/prefix` or a local folder."""
    if uri.startswith("s3://"):
        bucket, _, prefix = uri.removeprefix("s3://").partition("/")
        return S3Store(bucket, prefix, region)
    return LocalStore(Path(uri))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class IndexUnavailable(Exception):
    """Nothing usable is published: no `LATEST.json`, or one built with another embedding model."""


class IndexSource:
    """The published index for the agent: downloaded on first use, then checked for a newer one
    every `check_seconds` (one HEAD request). Not thread-safe: the retriever calls it under its lock.

    `changed()` downloads a newer index into its own folder under `cache_dir` and returns it; the
    caller opens it and calls `accept()`, or leaves the current one in use. An index built with
    another embedding model is refused: its vectors would not match the queries.
    """

    def __init__(
        self,
        store: IndexStore,
        cache_dir: Path,
        embedding_model: str,
        check_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.cache_dir = Path(cache_dir)
        self.embedding_model = embedding_model
        self.check_seconds = check_seconds
        self.clock = clock
        self.current: dict | None = None  # the accepted LATEST.json
        self.checked_at: str | None = None  # wall clock, for health
        self._etag: str | None = None
        self._checked: float | None = None
        self._pending: tuple[dict, str] | None = None

    def due(self) -> bool:
        return self.current is None or self._checked is None or self.clock() - self._checked >= self.check_seconds

    def changed(self) -> tuple[dict, Path] | None:
        """Check for a newer index; download it and return its `LATEST.json` and folder, or None."""
        self._checked = self.clock()
        self.checked_at = _now()
        if self.current is not None and self.store.etag() == self._etag:
            return None
        found = self.store.latest()
        if found is None:
            raise IndexUnavailable("no index is published yet (LATEST.json is missing)")
        meta, etag = found
        if self.current is not None and meta["index"] == self.current["index"]:
            self._etag = etag  # rewritten, same index
            return None
        if meta.get("embedding_model") != self.embedding_model:
            raise IndexUnavailable(
                f"index {meta['index']} was built with {meta.get('embedding_model')}, not {self.embedding_model}"
            )
        folder = self.cache_dir / meta["index"]
        if folder.exists():
            shutil.rmtree(folder)  # a download that failed part way
        self.store.download_index(meta, folder)
        self._pending = (meta, etag)
        return meta, folder

    def accept(self) -> None:
        """Mark the index `changed()` returned as the one in use."""
        self.current, self._etag = self._pending
        self._pending = None
