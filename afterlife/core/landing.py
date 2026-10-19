"""The landing zone: raw source bytes, exactly as fetched, never rewritten.

Layout, identical on a laptop and in S3:

    landing/<source>/snapshot_date=YYYY-MM-DD/<name>.gz
    landing/<source>/snapshot_date=YYYY-MM-DD/_manifest.json

The manifest records where the bytes came from, the sha256 of the bytes before
compression, and what the fetch cost. Everything downstream (bronze, silver,
gold, the serving table) can be rebuilt from this prefix alone, which is the
property ADR 0002 exists to protect.

Writes are idempotent and refuse to overwrite. Fetching the same snapshot twice
is a no op when the bytes match and an error when they do not, because a
source that silently changed a published snapshot is a finding, not a retry.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol


class ImmutableWriteError(RuntimeError):
    """Raised when a write would replace different bytes at an existing key."""


class Store(Protocol):
    def exists(self, key: str) -> bool: ...
    def read(self, key: str) -> bytes: ...
    def write_new(self, key: str, data: bytes) -> None: ...
    def list(self, prefix: str) -> list[str]: ...


class LocalStore:
    """A directory that behaves like an S3 prefix."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def uri(self, key: str) -> str:
        """Where a Spark reader finds this object."""
        return str(self._path(key).resolve())

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def write_new(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL gives the same guarantee as S3's If-None-Match: *.
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)

    def list(self, prefix: str) -> list[str]:
        base = self._path(prefix)
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file())


class S3Store:
    """The same contract on S3, using conditional writes for immutability.

    `If-None-Match: *` makes S3 reject a PUT when the key already exists, so
    two concurrent Lambda invocations cannot both land the same snapshot.
    """

    def __init__(self, bucket: str, client=None) -> None:
        if client is None:
            import boto3  # available in the Lambda runtime; not a core dependency

            client = boto3.client("s3")
        self.bucket = bucket
        self.client = client

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def write_new(self, key: str, data: bytes) -> None:
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, IfNoneMatch="*")
        except self.client.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"PreconditionFailed", "ConditionalRequestConflict"}:
                raise FileExistsError(key) from exc
            raise

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return sorted(keys)


@dataclass
class Manifest:
    source: str
    snapshot_date: str
    origin_url: str
    origin_version: str
    sha256: str
    raw_bytes: int
    stored_bytes: int
    object_key: str
    fetched_at: str = field(default_factory=lambda: utc_now())
    records: int | None = None
    cost_usd: float | None = None
    notes: dict = field(default_factory=dict)
    # "gzip": the landing step compressed the raw bytes. "identity": the source
    # was already compressed (PMC-ids.csv.gz) and is stored byte for byte.
    stored_as: str = "gzip"

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), indent=2, sort_keys=True).encode()

    @classmethod
    def from_json(cls, raw: bytes) -> Manifest:
        return cls(**json.loads(raw))


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def partition(source: str, snapshot_date: date | str) -> str:
    d = snapshot_date.isoformat() if isinstance(snapshot_date, date) else snapshot_date
    date.fromisoformat(d)  # reject anything that is not a calendar date
    return f"landing/{source}/snapshot_date={d}"


def land(
    store: Store,
    *,
    source: str,
    snapshot_date: date | str,
    name: str,
    raw: bytes,
    origin_url: str,
    origin_version: str,
    records: int | None = None,
    cost_usd: float | None = None,
    notes: dict | None = None,
    compress: bool = True,
    manifest_name: str = "_manifest.json",
) -> tuple[Manifest, bool]:
    """Land one object and its manifest. Returns (manifest, newly_written).

    The object is gzip compressed with a fixed mtime so the stored bytes are a
    pure function of the raw bytes, which keeps the operation idempotent.
    """
    prefix = partition(source, snapshot_date)
    object_key = f"{prefix}/{name}.gz" if compress else f"{prefix}/{name}"
    if not manifest_name.startswith("_manifest") or not manifest_name.endswith(".json"):
        raise ValueError("manifest names must look like _manifest*.json")
    manifest_key = f"{prefix}/{manifest_name}"
    digest = sha256(raw)

    if store.exists(manifest_key):
        existing = Manifest.from_json(store.read(manifest_key))
        if existing.sha256 == digest and existing.object_key == object_key:
            return existing, False
        raise ImmutableWriteError(
            f"{manifest_key} already holds sha256 {existing.sha256[:12]}, "
            f"refusing to replace it with {digest[:12]}"
        )

    stored = gzip.compress(raw, mtime=0) if compress else raw
    manifest = Manifest(
        source=source,
        snapshot_date=prefix.rsplit("=", 1)[1],
        origin_url=origin_url,
        origin_version=origin_version,
        sha256=digest,
        raw_bytes=len(raw),
        stored_bytes=len(stored),
        object_key=object_key,
        records=records,
        cost_usd=cost_usd,
        notes=notes or {},
        stored_as="gzip" if compress else "identity",
    )
    try:
        store.write_new(object_key, stored)
    except FileExistsError:
        # A previous attempt wrote the object and died before the manifest.
        existing_raw = store.read(object_key)
        if compress:
            existing_raw = gzip.decompress(existing_raw)
        if sha256(existing_raw) != digest:
            raise ImmutableWriteError(f"{object_key} exists with different bytes") from None
    store.write_new(manifest_key, manifest.to_json())
    return manifest, True


def manifests(store: Store, source: str) -> list[Manifest]:
    keys = [
        k
        for k in store.list(f"landing/{source}/")
        if k.rsplit("/", 1)[-1].startswith("_manifest") and k.endswith(".json")
    ]
    return [Manifest.from_json(store.read(k)) for k in keys]


def read_raw(store: Store, manifest: Manifest) -> bytes:
    raw = store.read(manifest.object_key)
    if manifest.stored_as == "gzip":
        raw = gzip.decompress(raw)
    if sha256(raw) != manifest.sha256:
        raise ValueError(f"checksum mismatch for {manifest.object_key}")
    return raw
