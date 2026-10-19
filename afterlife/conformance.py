"""Differential test of the gold layer across two SQL engines.

The same dbt models are built on DuckDB and on Spark SQL (the engine under
Databricks). Every row of every mart is then compared. "Portable across
warehouses" is a common claim in data engineering write ups and is rarely
checked; this checks it, and reports the differences it finds instead of
asserting there are none.

Values are normalized before comparison only where the engines legitimately
differ in representation: Decimal and float are compared at 6 decimal places,
and dates as ISO strings. Anything else that differs is a finding.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from decimal import Decimal

from afterlife import gold
from afterlife.export import _connect, _lake

MARTS = [
    "registry",
    "registry_agreement",
    "flag_exclusions",
    "retraction_lag",
    "afterlife_citations",
    "reason_trends",
    "record_mutability",
]


def _norm(v):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (Decimal, float)):
        return round(float(v), 6)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return tuple(_norm(x) for x in v)
    return v


def compare(a_cols, a_rows, b_cols, b_rows, sample: int = 3) -> dict:
    common = sorted(set(a_cols) & set(b_cols))
    ia = [a_cols.index(c) for c in common]
    ib = [b_cols.index(c) for c in common]
    ca = Counter(tuple(_norm(r[i]) for i in ia) for r in a_rows)
    cb = Counter(tuple(_norm(r[i]) for i in ib) for r in b_rows)
    only_a, only_b = ca - cb, cb - ca
    return {
        "rows_duckdb": len(a_rows),
        "rows_spark": len(b_rows),
        "columns_only_in_duckdb": sorted(set(a_cols) - set(b_cols)),
        "columns_only_in_spark": sorted(set(b_cols) - set(a_cols)),
        "rows_only_in_duckdb": sum(only_a.values()),
        "rows_only_in_spark": sum(only_b.values()),
        "identical": not only_a and not only_b and set(a_cols) == set(b_cols),
        "sample_only_in_duckdb": [dict(zip(common, r, strict=True)) for r in list(only_a)[:sample]],
        "sample_only_in_spark": [dict(zip(common, r, strict=True)) for r in list(only_b)[:sample]],
    }


def run(build: bool = True) -> dict:
    builds = {}
    if build:
        builds = {t: gold.run(target=t) for t in ("duckdb", "spark")}
        failed = {t: b for t, b in builds.items() if b["exit_code"] != 0}
        if failed:
            return {"builds": builds, "error": "a dbt build failed; nothing compared"}

    con = _connect()
    from afterlife.spark.session import Lake, get_spark

    spark = get_spark("afterlife-conformance")
    lake = Lake(root=f"{_lake()}")
    results = {}
    for mart in MARTS:
        cur = con.execute(f"select * from gold.{mart}")
        a_cols = [c[0] for c in cur.description]
        a_rows = cur.fetchall()
        df = lake.read(spark, "gold", mart)
        b_cols = df.columns
        b_rows = [tuple(r) for r in df.collect()]
        results[mart] = compare(a_cols, a_rows, b_cols, b_rows)
    summary = {
        "marts": len(results),
        "identical": sum(1 for r in results.values() if r["identical"]),
        "rows_compared": sum(r["rows_duckdb"] for r in results.values()),
    }
    return {
        "summary": summary,
        "builds": {
            t: {k: b.get(k) for k in ("exit_code", "elapsed_s", "status_counts")}
            for t, b in builds.items()
        },
        "marts": results,
    }
