"""Export gold for the three consumers: the AWS serving table, Athena, and EvidenceOps.

    <out>/registry/as_of=YYYY-MM-DD/_coverage.json              what each source covered
    <out>/registry/as_of=YYYY-MM-DD/parquet/registry.parquet    flat rows for Athena
    <out>/registry/as_of=YYYY-MM-DD/registry.jsonl.gz           one document per paper
    <out>/evidenceops/trust_registry.jsonl              EvidenceOps TrustRecord lines

The document shape is a contract with aws/lambdas/load_registry and
aws/lambdas/api; change it in both places or neither.

EvidenceOps keys trust by PMCID and has four statuses. A paper this registry
calls `disputed` is exported to EvidenceOps as `concerned`, which EvidenceOps
treats as not citable. Disagreement between registries about whether a paper
stands is itself a reason not to rest a claim on it.
"""

from __future__ import annotations

import gzip
import json
import os
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

from afterlife.core.landing import LocalStore, manifests
from afterlife.gold import ROOT

# Per paper lists in the documents that the flat parquet leaves out.
DETAIL_COLUMNS = ("record_ids", "rw_natures", "reasons")

EVIDENCEOPS_STATUS = {
    "retracted": "retracted",
    "disputed": "concerned",
    "concern": "concerned",
    "corrected": "corrected",
    "reinstated": "ok",
}


def _connect():
    import duckdb

    path = os.environ.get("AFTERLIFE_DUCKDB", str(ROOT / "data" / "gold" / "afterlife.duckdb"))
    con = duckdb.connect(path, read_only=True)
    con.execute("INSTALL delta; LOAD delta;")
    return con


def _lake() -> str:
    return str(Path(os.environ.get("AFTERLIFE_LAKE", ROOT / "data" / "lake")).resolve())


QUERY = """
with rw_detail as (
    select
        i.paper_key,
        list(distinct n.record_id order by n.record_id) as record_ids,
        list(distinct n.nature order by n.nature) as natures,
        list(distinct r order by r) as reasons
    from gold.int_paper_identity i
    join delta_scan('{lake}/silver/rw_notices') n
      on i.source = 'retraction_watch' and i.source_key = n.paper_key
    left join unnest(n.reasons) as u(r) on true
    group by i.paper_key
)
select r.*, d.record_ids, d.natures as rw_natures, d.reasons
from gold.registry r
left join rw_detail d using (paper_key)
order by r.paper_key
"""


def _iso(v) -> str | None:
    return v.isoformat() if isinstance(v, date) else v


def document(row: dict, as_of: str) -> dict:
    flags = {
        "retraction_watch": row["rw_status"],
        "openalex": bool(row["flag_openalex"]),
        "pubmed": bool(row["flag_pubmed"]),
        "pmc": row["flag_pmc"],
    }
    # Registries that could have flagged this paper: Retraction Watch and
    # OpenAlex always; PubMed only with a PMID; PMC only with a linked copy.
    observed = 2 + (1 if row["pmid"] else 0) + (1 if row["flag_pmc"] is not None else 0)
    rw = None
    if row["rw_status"]:
        rw = {
            "status": row["rw_status"],
            "first_retraction_date": _iso(row["first_retraction_date"]),
            "record_ids": [int(x) for x in (row["record_ids"] or [])],
            "natures": list(row["rw_natures"] or []),
            "reasons": [r for r in (row["reasons"] or []) if r],
        }
    return {
        "paper_key": row["paper_key"],
        "ids": {
            "doi": row["doi"],
            "pmid": row["pmid"],
            "pmcid": row["pmcid"],
            "openalex": row["openalex_id"],
        },
        "title": row["title"],
        "verdict": row["verdict"],
        "flags": flags,
        "retraction_watch": rw,
        "agreement": {"flagging": int(row["n_flagging"]), "observed": observed},
        "as_of": as_of,
    }


def coverage(as_of: str, docs: list[dict], landing_root: str | None = None) -> dict:
    root = landing_root or os.environ.get("AFTERLIFE_LANDING", str(ROOT / "data"))
    store = LocalStore(root)

    def newest(source: str):
        ms = sorted(manifests(store, source), key=lambda m: m.snapshot_date)
        return ms[-1] if ms else None

    by_verdict: dict[str, int] = {}
    for d in docs:
        by_verdict[d["verdict"]] = by_verdict.get(d["verdict"], 0) + 1
    out = {
        "as_of": as_of,
        "registry": {"papers": len(docs), "by_verdict": by_verdict},
        "sources": {},
    }
    for source in ("retraction_watch", "openalex", "pubmed", "pmc_metadata"):
        m = newest(source)
        if m is None:
            continue
        notes = {k: v for k, v in (m.notes or {}).items() if k not in {"slices", "pending"}}
        out["sources"][source] = {"snapshot_date": m.snapshot_date, "records": m.records, **notes}
    return out


def trust_records(docs: list[dict], as_of: str) -> list[dict]:
    """EvidenceOps TrustRecord lines, for every paper with a PMCID."""
    trust = []
    for d in docs:
        if not d["ids"]["pmcid"]:
            continue
        sources = [k for k, v in d["flags"].items() if v not in (None, False)]
        rw = d["retraction_watch"] or {}
        trust.append(
            {
                "pmcid": d["ids"]["pmcid"],
                "status": EVIDENCEOPS_STATUS[d["verdict"]],
                "notice_pmcid": "",
                "notice_date": rw.get("first_retraction_date"),
                "notice_title": (
                    f"afterlife {as_of}: {d['verdict']}, flagged by {', '.join(sources)}"
                ),
            }
        )
    return trust


def write(
    out: Path,
    docs: list[dict],
    as_of: str,
    write_parquet: Callable[[Path], None],
    landing_root: str | None = None,
) -> dict:
    """The write half, shared by the laptop and the Databricks export task.

    Each engine supplies only its query and a function that writes one
    parquet file to the path it is given. Layout and order live here:

    * `_coverage.json` first. On S3 the loader Lambda fires on
      registry.jsonl.gz and reads the coverage file beside it at that moment.
    * `parquet/registry.parquet` in its own prefix, so Athena's partition
      projection (which supplies as_of from the path) never reads the JSONL.
    * `registry.jsonl.gz` last, as the trigger.
    """
    reg = Path(out) / "registry" / f"as_of={as_of}"
    (reg / "parquet").mkdir(parents=True, exist_ok=True)
    cov = coverage(as_of, docs, landing_root)
    (reg / "_coverage.json").write_text(json.dumps(cov, indent=2, default=str) + "\n")
    write_parquet(reg / "parquet" / "registry.parquet")

    trust = trust_records(docs, as_of)
    eo = Path(out) / "evidenceops"
    eo.mkdir(parents=True, exist_ok=True)
    (eo / "trust_registry.jsonl").write_text(
        "".join(json.dumps(t, sort_keys=True) + "\n" for t in trust)
    )
    payload = "".join(json.dumps(d, sort_keys=True, ensure_ascii=False) + "\n" for d in docs)
    (reg / "registry.jsonl.gz").write_bytes(gzip.compress(payload.encode(), mtime=0))
    return {
        "as_of": as_of,
        "documents": len(docs),
        "registry_dir": str(reg),
        "evidenceops_records": len(trust),
        "coverage": cov,
    }


def run(out: Path) -> dict:
    con = _connect()
    query = QUERY.format(lake=_lake())
    cur = con.execute(query)
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    as_of = datetime.now(UTC).date().isoformat()
    docs = [document(r, as_of) for r in rows]
    flat = f"select * exclude ({', '.join(DETAIL_COLUMNS)}) from ({query})"

    def duckdb_parquet(path: Path) -> None:
        con.execute(f"copy ({flat}) to '{path}' (format parquet)")

    return write(out, docs, as_of, duckdb_parquet)
