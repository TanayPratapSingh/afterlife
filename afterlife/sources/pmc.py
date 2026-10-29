"""PubMed Central: the fourth registry, read straight from AWS Open Data.

NLM publishes the PMC article datasets in a public S3 bucket,
`s3://pmc-oa-opendata`, with one small JSON metadata object per article
version that carries an `is_retracted` flag. There are millions of them, so
this module does not read them all. It reads the ones that matter: every PMC
article that any of the other three registries mentions.

Two steps:

1. `PMC-ids.csv.gz` from the NCBI FTP site maps every PMC article to its PMID
   and DOI. It is landed byte for byte, since it is already compressed.
2. For each target PMCID, fetch `metadata/<PMCID>.<version>.json`. Most
   articles are version 1; when that key is missing, one LIST call finds the
   newest version. A target with no metadata object at all is not in the open
   data subset (for example, a non open access article), and that is
   recorded, not dropped, because "PMC has no flag" and "PMC has no copy" are
   different claims.

The full coverage version, every article in the bucket, is a Step Functions
Distributed Map over the bucket's own daily S3 Inventory. It is described in
docs/ARCHITECTURE.md and has not been run.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from afterlife.core import http, ids
from afterlife.core.landing import Manifest, Store, land, manifests, read_raw

IDS_URL = "https://ftp.ncbi.nlm.nih.gov/pub/pmc/PMC-ids.csv.gz"
BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"
KEEP = (
    "pmcid",
    "version",
    "pmid",
    "doi",
    "is_retracted",
    "license_code",
    "is_pmc_openaccess",
    "is_manuscript",
    "is_historical_ocr",
    "title",
    "citation",
)


def land_id_map(
    store: Store, snapshot_date: date | None = None, get=http.get
) -> tuple[Manifest, bool]:
    resp = get(IDS_URL, timeout=600, headers={"Accept-Encoding": "identity"})
    raw = resp.body
    return land(
        store,
        source="pmc_ids",
        snapshot_date=snapshot_date or date.today(),
        name="PMC-ids.csv.gz",
        raw=raw,
        origin_url=IDS_URL,
        origin_version=resp.headers.get("Last-Modified", "unknown"),
        compress=False,
        notes={"last_modified": resp.headers.get("Last-Modified")},
    )


def _latest_version_key(pmcid: str, get) -> str | None:
    body = get(f"{BUCKET}/", {"list-type": 2, "prefix": f"metadata/{pmcid}."}).body.decode()
    versions = [int(v) for v in re.findall(rf"<Key>metadata/{pmcid}\.(\d+)\.json</Key>", body)]
    return f"metadata/{pmcid}.{max(versions)}.json" if versions else None


def fetch_one(pmcid: str, get=http.get) -> dict:
    """Metadata for one PMCID, or a row saying it is not in the open data."""
    key = f"metadata/{pmcid}.1.json"
    try:
        doc = get(f"{BUCKET}/{key}", attempts=4).json()
    except http.HttpError as exc:
        if exc.status not in (403, 404):
            raise
        key = _latest_version_key(pmcid, get)
        if key is None:
            return {"pmcid": pmcid, "in_open_data": False}
        doc = get(f"{BUCKET}/{key}", attempts=4).json()
    row = {k: doc.get(k) for k in KEEP}
    row["pmcid"] = pmcid
    row["in_open_data"] = True
    row["metadata_key"] = key
    return row


def fetch_metadata(pmcids: Iterable[str], *, workers: int = 16, get=http.get) -> list[dict]:
    ids = sorted(set(pmcids))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda p: fetch_one(p, get=get), ids))


def land_metadata(
    store: Store,
    pmcids: Iterable[str],
    *,
    workers: int = 16,
    snapshot_date: date | None = None,
    get=http.get,
) -> tuple[Manifest, bool]:
    rows = fetch_metadata(pmcids, workers=workers, get=get)
    raw = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode()
    found = [r for r in rows if r["in_open_data"]]
    return land(
        store,
        source="pmc_metadata",
        snapshot_date=snapshot_date or date.today(),
        name="metadata.jsonl",
        raw=raw,
        origin_url=f"{BUCKET}/metadata/",
        origin_version="s3-open-data",
        records=len(rows),
        cost_usd=0.0,
        notes={
            "requested": len(rows),
            "in_open_data": len(found),
            "not_in_open_data": len(rows) - len(found),
            "flagged_retracted": sum(1 for r in found if r.get("is_retracted")),
            "version_gt_1": sum(1 for r in found if not r["metadata_key"].endswith(".1.json")),
        },
    )


def fetch_document(pmcid: str, get=http.get) -> dict:
    """`fetch_one` plus the article's JATS XML, which sits next to its metadata.
    `xml` is None when the bucket holds metadata but no XML file."""
    row = fetch_one(pmcid, get=get)
    if row["in_open_data"]:
        stem = row["metadata_key"].removeprefix("metadata/").removesuffix(".json")
        try:
            row["xml"] = get(f"{BUCKET}/{stem}/{stem}.xml", attempts=4).body.decode()
        except http.HttpError as exc:
            if exc.status not in (403, 404):
                raise
            row["xml"] = None
    return row


def lookup(
    store: Store, pmids: Iterable[str] = (), dois: Iterable[str] = (), pmcids: Iterable[str] = ()
) -> dict[tuple[str, str], dict]:
    """Rows of the newest landed PMC-ids.csv for the identifiers asked for, keyed
    by ("pmid", v), ("doi", v) or ("pmcid", v). Streams the file; holding all of
    it would take gigabytes."""
    want = {("pmid", p) for p in pmids} | {("doi", d) for d in dois}
    want |= {("pmcid", p) for p in pmcids}
    newest = max(manifests(store, "pmc_ids"), key=lambda m: m.snapshot_date)
    out: dict[tuple[str, str], dict] = {}
    with gzip.open(io.BytesIO(read_raw(store, newest)), "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            row = {
                "pmcid": ids.pmcid(r["PMCID"]),
                "pmid": ids.pmid(r["PMID"]),
                "doi": ids.doi(r["DOI"]),
                "year": r["Year"],
            }
            for key in (("pmcid", row["pmcid"]), ("pmid", row["pmid"]), ("doi", row["doi"])):
                if key in want:
                    out[key] = row
    return out
