"""Tests for the index store (AD-22): the S3 layout, on a local folder and on a stub S3 client."""

import io

from fakes import publish_index

from assistant.index_store import LATEST, LocalStore, S3Store, open_store


def test_a_published_index_can_be_read_back_from_a_local_store(tmp_path):
    store = LocalStore(tmp_path / "store")
    assert store.latest() is None and store.etag() is None

    latest = publish_index(store, tmp_path, ["Pay by BPAY."], "abc123")

    meta, etag = store.latest()
    assert meta == latest and etag == store.etag()
    assert meta["files"] and not any(f.endswith(".lock") for f in meta["files"])
    loaded = store.download_index(meta, tmp_path / "loaded")
    assert sorted(str(p.relative_to(loaded)) for p in loaded.rglob("*") if p.is_file()) == meta["files"]
    assert (tmp_path / "store" / "content" / "abc123" / "p.md").read_text().startswith("---")


def test_the_etag_changes_when_a_new_index_is_published(tmp_path):
    store = LocalStore(tmp_path / "store")
    publish_index(store, tmp_path / "a", ["one"], "aaa")
    first = store.etag()

    publish_index(store, tmp_path / "b", ["two"], "bbb")

    assert store.etag() != first


class _MissingKey(Exception):
    response = {"Error": {"Code": "404"}}


class _S3:
    """Stub of the boto3 S3 client, recording the order of writes."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.writes: list[str] = []

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _MissingKey()
        return {"Body": io.BytesIO(self.objects[Key]), "ETag": f'"{hash(self.objects[Key])}"'}

    def head_object(self, Bucket, Key):
        return {"ETag": self.get_object(Bucket, Key)["ETag"]}

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = Body
        self.writes.append(Key)

    def upload_file(self, Filename, Bucket, Key):
        with open(Filename, "rb") as f:
            self.put_object(Bucket, Key, f.read())

    def download_file(self, Bucket, Key, Filename):
        with open(Filename, "wb") as f:
            f.write(self.objects[Key])


def test_s3_writes_latest_json_last_under_the_prefix(tmp_path):
    client = _S3()
    store = S3Store("bucket", "content/", "ap-southeast-2", client=client)
    assert store.latest() is None and store.etag() is None

    latest = publish_index(store, tmp_path, ["Pay by BPAY."], "abc123")

    assert client.writes[-1] == f"content/{LATEST}"
    assert all(key.startswith("content/") for key in client.writes)
    assert {f"content/indexes/{latest['index']}/{name}" for name in latest["files"]} <= set(client.writes)
    assert client.writes.index("content/content/abc123/p.md") < client.writes.index(
        f"content/indexes/{latest['index']}/{latest['files'][0]}"
    )
    assert store.latest()[0] == latest
    store.download_index(latest, tmp_path / "loaded")
    assert (tmp_path / "loaded" / latest["files"][0]).exists()


def test_open_store_reads_an_s3_uri_or_a_folder(tmp_path):
    s3 = open_store("s3://bucket/content", "ap-southeast-2")
    assert isinstance(s3, S3Store) and (s3.bucket, s3.prefix) == ("bucket", "content")
    assert isinstance(open_store(str(tmp_path), "ap-southeast-2"), LocalStore)
