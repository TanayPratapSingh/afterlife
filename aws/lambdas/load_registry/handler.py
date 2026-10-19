"""Load a registry export into DynamoDB, one item per identifier alias.

Triggered by S3 ObjectCreated on `export/registry/*.jsonl.gz`. Each line of
the export is one paper's registry document. A reader may ask about that paper
by DOI, PMID, PMCID, or OpenAlex id, so the document is written once under each
alias it has:

    pk = doi#10.1000/xyz      doc = <the JSON line>, paper_key, as_of
    pk = pmid#12345           doc = <the same JSON line>, ...

Aliases are normalized with `afterlife.core.ids`, the same functions the API
uses to classify a query. Writer and reader sharing one implementation is what
guarantees a lookup key matches a stored key; the export may already carry
normalized ids, and normalizing twice is a no op.

Three properties the API relies on:

* All or nothing validation. The whole export is parsed and checked before the
  first write, so a contract violation on line 70,000 leaves the table as it
  was rather than half updated.
* Shared aliases are kept, not lost. Retraction Watch has records where one
  PMID sits on two DOIs. When two papers claim the same alias, the item keeps
  the more severe verdict (a false "not retracted" is the failure this project
  exists to prevent) and lists the other paper keys in `also_paper_keys`.
* `meta#coverage` is written last. The API dates every "not flagged" answer by
  that item, so it must never name an export whose items are not all present.
  An export older than the one already loaded is skipped.

Items carry `expires_at` (as_of plus ITEM_TTL_DAYS) for DynamoDB TTL, so a
paper that drops out of the export ages out instead of being served forever.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import os
import re
import urllib.parse
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import boto3

from afterlife.core import ids

log = logging.getLogger()
log.setLevel(logging.INFO)

COVERAGE_PK = "meta#coverage"
VERDICTS = ("retracted", "concern", "corrected", "reinstated", "disputed")
# Collision tie break: the verdict that would do the most harm to miss wins.
SEVERITY = {"retracted": 4, "concern": 3, "disputed": 2, "corrected": 1, "reinstated": 0}
AS_OF_IN_KEY = re.compile(r"(?:^|/)as_of=(\d{4}-\d{2}-\d{2})/")
ALIASES = (
    ("doi", ids.doi),
    ("pmid", ids.pmid),
    ("pmcid", ids.pmcid),
    ("openalex", ids.openalex_id),
)


class ContractError(ValueError):
    """The export does not match the registry contract. Nothing was written."""


def aliases(doc: dict[str, Any]) -> list[str]:
    raw = doc.get("ids") or {}
    out = []
    for kind, normalize in ALIASES:
        value = raw.get(kind)
        norm = normalize(str(value)) if value is not None else None
        if norm:
            out.append(f"{kind}#{norm}")
    return out


def as_of_from_key(key: str) -> str:
    m = AS_OF_IN_KEY.search(key)
    if not m:
        raise ContractError(f"{key} is not under an as_of=YYYY-MM-DD/ prefix")
    date.fromisoformat(m.group(1))
    return m.group(1)


def expires_at(as_of: str, ttl_days: int) -> int:
    day = datetime.combine(date.fromisoformat(as_of), time(), UTC) + timedelta(days=ttl_days)
    return int(day.timestamp())


def check(doc: Any, line_no: int, as_of: str) -> list[str]:
    if not isinstance(doc, dict):
        return [f"line {line_no}: not a JSON object"]
    problems = []
    if not doc.get("paper_key"):
        problems.append(f"line {line_no}: missing paper_key")
    if doc.get("verdict") not in VERDICTS:
        problems.append(f"line {line_no}: verdict {doc.get('verdict')!r} not in {VERDICTS}")
    if doc.get("as_of") not in (None, as_of):
        problems.append(f"line {line_no}: as_of {doc.get('as_of')!r} != {as_of} from the key")
    if not isinstance(doc.get("ids"), dict):
        problems.append(f"line {line_no}: ids is not an object")
    return problems


def plan_items(lines: io.TextIOBase, as_of: str, ttl_days: int) -> tuple[dict[str, dict], dict]:
    """Parse and validate the export, returning (items by pk, stats).

    Raises ContractError listing the first problems found, before any write.
    """
    chosen: dict[str, tuple[int, str, str]] = {}  # pk -> (severity, paper_key, doc json)
    claimants: dict[str, set[str]] = {}
    problems: list[str] = []
    docs = without_ids = 0
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"line {line_no}: invalid JSON ({exc.msg})")
            continue
        found = check(doc, line_no, as_of)
        if found:
            problems.extend(found)
            continue
        docs += 1
        keys = aliases(doc)
        if not keys:
            without_ids += 1
            continue
        doc.setdefault("as_of", as_of)
        body = json.dumps(doc, sort_keys=True, separators=(",", ":"))
        rank = (SEVERITY[doc["verdict"]], doc["paper_key"], body)
        for pk in keys:
            claimants.setdefault(pk, set()).add(doc["paper_key"])
            # Highest severity wins; equal severity falls back to paper_key so
            # the result does not depend on line order.
            if pk not in chosen or rank > chosen[pk]:
                chosen[pk] = rank
    if problems:
        shown = "; ".join(problems[:10])
        raise ContractError(f"{len(problems)} contract problems, nothing written: {shown}")

    ttl = expires_at(as_of, ttl_days)
    items: dict[str, dict] = {}
    for pk, (_, paper_key, body) in chosen.items():
        item = {"pk": pk, "doc": body, "paper_key": paper_key, "as_of": as_of, "expires_at": ttl}
        others = sorted(claimants[pk] - {paper_key})
        if others:
            item["also_paper_keys"] = others
        items[pk] = item
    stats = {
        "documents": docs,
        "documents_without_ids": without_ids,
        "items": len(items),
        "shared_aliases": sum(1 for v in claimants.values() if len(v) > 1),
    }
    return items, stats


def write_items(table_name: str, items: list[dict], workers: int) -> int:
    """Write with one batch_writer per worker thread.

    batch_writer groups puts into BatchWriteItem calls of 25 and resends
    unprocessed items. Items are sharded by key so no two threads write the
    same pk. Sessions and resources are not thread safe, so each thread builds
    its own session.
    """
    shards: list[list[dict]] = [[] for _ in range(max(1, workers))]
    for item in items:
        shards[zlib.crc32(item["pk"].encode()) % len(shards)].append(item)

    def run(shard: list[dict]) -> int:
        table = boto3.session.Session().resource("dynamodb").Table(table_name)
        with table.batch_writer(overwrite_by_pkeys=["pk"]) as batch:
            for item in shard:
                batch.put_item(Item=item)
        return len(shard)

    with ThreadPoolExecutor(max_workers=len(shards)) as pool:
        return sum(pool.map(run, [s for s in shards if s]))


def read_coverage(s3, bucket: str, key: str) -> dict | None:
    """The export's sibling `_coverage.json`, or None when it is absent.

    Presence is checked by listing the exact prefix rather than by catching a
    failed GET: without s3:ListBucket, S3 answers a GET for a missing key with
    403, and treating every 403 as "absent" would hide a broken policy.
    """
    sibling = key.rsplit("/", 1)[0] + "/_coverage.json"
    listed = s3.list_objects_v2(Bucket=bucket, Prefix=sibling, MaxKeys=1).get("Contents", [])
    if not any(obj["Key"] == sibling for obj in listed):
        return None
    return json.loads(s3.get_object(Bucket=bucket, Key=sibling)["Body"].read())


def load(bucket: str, key: str, *, table_name: str, ttl_days: int, workers: int) -> dict:
    as_of = as_of_from_key(key)
    table = boto3.resource("dynamodb").Table(table_name)
    current = table.get_item(Key={"pk": COVERAGE_PK}).get("Item")
    if current and current.get("as_of", "") > as_of:
        return {"key": key, "as_of": as_of, "skipped": f"older than loaded {current['as_of']}"}

    s3 = boto3.client("s3")
    body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    with gzip.GzipFile(fileobj=body) as gz:
        items, stats = plan_items(io.TextIOWrapper(gz, encoding="utf-8"), as_of, ttl_days)
    written = write_items(table_name, list(items.values()), workers)

    coverage = read_coverage(s3, bucket, key)
    meta = {
        "as_of": as_of,
        "export_key": key,
        "loaded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coverage_file_present": coverage is not None,
        "coverage": coverage,
        "load": {**stats, "items_written": written},
    }
    if coverage and coverage.get("as_of") not in (None, as_of):
        meta["coverage_as_of_mismatch"] = coverage.get("as_of")
    table.put_item(
        Item={
            "pk": COVERAGE_PK,
            "doc": json.dumps(meta, sort_keys=True),
            "paper_key": COVERAGE_PK,
            "as_of": as_of,
        }
    )
    return {"key": key, **meta["load"], "as_of": as_of, "coverage_item": True}


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    table_name = os.environ["TABLE_NAME"]
    ttl_days = int(os.environ.get("ITEM_TTL_DAYS", "7"))
    workers = int(os.environ.get("WRITE_WORKERS", "4"))
    results = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        # Keys in S3 event notifications are URL encoded: as_of= arrives as as_of%3D.
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        if not key.endswith(".jsonl.gz"):
            results.append({"key": key, "skipped": "not a .jsonl.gz export"})
            continue
        results.append(load(bucket, key, table_name=table_name, ttl_days=ttl_days, workers=workers))
    summary = {"exports": results, "items_written": sum(r.get("items_written", 0) for r in results)}
    log.info(json.dumps(summary, default=str))
    return summary
