"""Spark jobs, on synthetic data always and on the real lake when it exists.

The real data tests are the ones that matter most. They hold the Spark native
expressions to the Python reference implementations over every identifier
and every paper actually landed, which is where regex dialect differences and
status edge cases would show up. They skip, not fail, on a fresh clone.
"""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

import pytest

pyspark = pytest.importorskip("pyspark")
pytest.importorskip("delta")

from pyspark.sql import functions as F  # noqa: E402

from afterlife.core import ids  # noqa: E402
from afterlife.core.landing import LocalStore, land  # noqa: E402
from afterlife.core.status import fold, parse_nature  # noqa: E402
from afterlife.sources.retraction_watch import EXPECTED_COLUMNS  # noqa: E402
from afterlife.spark import bronze, silver  # noqa: E402
from afterlife.spark import expressions as X  # noqa: E402
from afterlife.spark.session import Lake, get_spark  # noqa: E402

pytestmark = pytest.mark.spark

REAL_LAKE = Path(os.environ.get("AFTERLIFE_LAKE", "data/lake"))
has_real = pytest.mark.skipif(
    not (REAL_LAKE / "silver" / "rw_paper_status" / "_delta_log").exists(),
    reason="no real lake under data/lake (run the pipeline first)",
)


@pytest.fixture(scope="module")
def spark():
    try:
        s = get_spark("afterlife-tests", shuffle_partitions=2)
    except Exception as exc:  # pragma: no cover - no Java runtime
        pytest.skip(f"Spark unavailable: {exc}")
    yield s


# Expressions against the Python reference -----------------------------------

EDGE_DOIS = [
    "10.1000/ABC",
    "https://doi.org/10.1000/x",
    "doi:10.1000/x",
    " 10.1000/x ",
    "Unavailable",
    "",
    None,
    "10.1061/(ASCE)0733-9399(2010)136:2(174)",
    "10.1000.5/x",
    "10.1/x",
    "11.1000/x",
    "10.1000/a b",
    "10.1000/é",
    "http://dx.doi.org/10.1000/Y",
    "https:/doi.org/10.1000/collapsed",
    "http:/dx.doi.org/10.1000/collapsed",
    # Whitespace Python strips and Spark's trim does not: no break space, tab,
    # line feed, the ASCII separators Python counts as whitespace, and the
    # ideographic space.
    " 10.1000/nbsp ",
    "\t10.1000/tab\n",
    "10.1000/inner space",
    "\x1f10.1000/unit",
    "　10.1000/ideographic",
]
EDGE_PMIDS = [
    "\u00a0123\u00a0",
    "\t42\n",
    "0",
    "00000000",
    "123",
    "123.0",
    None,
    "",
    "https://pubmed.ncbi.nlm.nih.gov/42/",
    "a1",
]


def test_doi_and_pmid_expressions_match_python_on_edge_cases(spark):
    df = spark.createDataFrame([(d,) for d in EDGE_DOIS], "v string")
    got = [r[0] for r in df.select(X.doi("v")).collect()]
    assert got == [ids.doi(d) for d in EDGE_DOIS]
    df = spark.createDataFrame([(p,) for p in EDGE_PMIDS], "v string")
    got = [r[0] for r in df.select(X.pmid("v")).collect()]
    assert got == [ids.pmid(p) for p in EDGE_PMIDS]


@has_real
def test_doi_expression_matches_python_on_every_landed_doi(spark):
    lake = Lake(root=str(REAL_LAKE))
    rw = lake.read(spark, "bronze", "retraction_watch")
    oa = lake.read(spark, "bronze", "openalex_works")
    raw = (
        rw.select(F.col("original_paper_doi").alias("v"))
        .union(rw.select("retraction_doi"))
        .union(oa.select("doi"))
        .distinct()
    )
    rows = raw.select("v", X.doi("v").alias("spark")).collect()
    mismatches = [(r.v, r.spark, ids.doi(r.v)) for r in rows if r.spark != ids.doi(r.v)]
    assert len(rows) > 100_000
    assert mismatches == [], mismatches[:10]


@has_real
def test_pmid_expression_matches_python_on_every_landed_pmid(spark):
    lake = Lake(root=str(REAL_LAKE))
    rw = lake.read(spark, "bronze", "retraction_watch")
    raw = (
        rw.select(F.col("original_paper_pub_med_id").alias("v"))
        .union(rw.select("retraction_pub_med_id"))
        .union(lake.read(spark, "bronze", "pubmed_flags").select("pmid"))
        .distinct()
    )
    rows = raw.select("v", X.pmid("v").alias("spark")).collect()
    mismatches = [(r.v, r.spark, ids.pmid(r.v)) for r in rows if r.spark != ids.pmid(r.v)]
    assert mismatches == [], mismatches[:10]


@has_real
def test_spark_status_matches_python_fold_on_every_paper(spark):
    """The closed form CASE in silver.paper_status against core.status.fold."""
    lake = Lake(root=str(REAL_LAKE))
    events = defaultdict(list)
    for r in (
        lake.read(spark, "silver", "rw_notices")
        .select("paper_key", "nature", "notice_date")
        .collect()
    ):
        n = parse_nature(r.nature)
        if n is not None:
            events[r.paper_key].append((n, r.notice_date))
    spark_status = {
        r.paper_key: r.status
        for r in lake.read(spark, "silver", "rw_paper_status")
        .select("paper_key", "status")
        .collect()
    }
    diffs = [
        (k, spark_status.get(k), fold(v).value)
        for k, v in events.items()
        if spark_status.get(k) != fold(v).value
    ]
    assert len(events) > 60_000
    assert diffs == [], diffs[:10]


# SCD2 replay on synthetic snapshots -------------------------------------------

COLS = [bronze.snake(c) for c in EXPECTED_COLUMNS]


def _row(rid, title="T", reason="+Plagiarism;", doi="10.1000/a", inst="A; B", date="1/2/2020 0:00"):
    r = dict.fromkeys(COLS)
    r.update(
        record_id=rid,
        title=title,
        institution=inst,
        reason=reason,
        original_paper_doi=doi,
        retraction_date=date,
        original_paper_date="1/1/2019 0:00",
        retraction_nature="Retraction",
        original_paper_pub_med_id="0",
    )
    return r


def _bronze(spark, snapshots: dict[str, list[dict]]):
    rows = [{**r, "_snapshot_date": d} for d, rs in snapshots.items() for r in rs]
    schema = ", ".join(f"{c} string" for c in COLS) + ", _snapshot_date string"
    return spark.createDataFrame(rows, schema).withColumn(
        "_snapshot_date", F.to_date("_snapshot_date")
    )


def test_scd2_replay_semantics(spark, tmp_path):
    lake = Lake(root=str(tmp_path / "lake"))
    beta = "Z\u00fcrich \u03b2 study"  # u-umlaut is in cp1252, beta is not
    snaps = {
        "2026-01-01": [_row("1"), _row("2"), _row("3", title=beta), _row("5")],
        # 1: reformatted only (the + prefix dropped, a line break in a cell).
        # 2: a real edit to the DOI. 4: inserted. 5: deleted.
        "2026-01-02": [
            _row("1", reason="Plagiarism;", inst="A;\nB"),
            _row("2", doi="10.1000/b"),
            _row("3", title=beta),
            _row("4"),
        ],
        # The lossy export: beta became '?' in record 3, a current record.
        "2026-01-03": [
            _row("1", reason="Plagiarism;"),
            _row("2", doi="10.1000/b"),
            _row("3", title="Z\u00fcrich ? study"),
            _row("4"),
        ],
        # 5 reappears; 3 is intact again.
        "2026-01-04": [
            _row("1", reason="Plagiarism;"),
            _row("2", doi="10.1000/b"),
            _row("3", title=beta),
            _row("4"),
            _row("5"),
        ],
    }
    lake.write(_bronze(spark, snaps), "bronze", "retraction_watch")
    old = silver.LOSSY_RECORDS_MAX
    silver.LOSSY_RECORDS_MAX = 0
    try:
        report = {r["snapshot_date"]: r for r in silver.build_history(spark, lake)}
    finally:
        silver.LOSSY_RECORDS_MAX = old

    assert report["2026-01-02"].get("update") == 1 and report["2026-01-02"].get("insert") == 1
    assert report["2026-01-02"]["deleted"] == 1
    assert report["2026-01-02"]["format_only"] == 1
    assert report["2026-01-03"]["quarantined"] is True
    assert report["2026-01-03"]["lossy_records"] == 1
    assert report["2026-01-04"].get("reappear") == 1

    h = lake.read(spark, "silver", "rw_history")
    versions = {r.record_id: [] for r in h.select("record_id").distinct().collect()}
    for r in h.orderBy("valid_from").collect():
        versions[r.record_id].append(r)
    assert len(versions["1"]) == 1, "a format only change must not cut a version"
    assert versions["1"][0].reason == "Plagiarism;", "type 1 refresh keeps the newest bytes"
    assert [v.original_paper_doi for v in versions["2"]] == ["10.1000/a", "10.1000/b"]
    assert [v.end_reason for v in versions["5"]] == ["deleted", None]
    assert len(versions["3"]) == 1 and "?" not in versions["3"][0].title, (
        "the quarantined snapshot's bytes must never reach the history"
    )

    changes = silver.build_changes(spark, lake)
    by_type = {(r.record_id, r.change_type): r.changed_columns for r in changes.collect()}
    assert by_type[("2", "update")] == ["original_paper_doi"]
    assert ("5", "delete") in by_type and ("5", "reappear") in by_type

    assert silver.build_history(spark, lake) == [], "replay is idempotent"


def test_out_of_order_snapshot_is_refused(spark, tmp_path):
    lake = Lake(root=str(tmp_path / "lake"))
    lake.write(_bronze(spark, {"2026-01-02": [_row("1")]}), "bronze", "retraction_watch")
    silver.build_history(spark, lake)
    late = _bronze(spark, {"2026-01-01": [_row("1")]})
    lake.write(late, "bronze", "retraction_watch", mode="append")
    with pytest.raises(ValueError, match="older than the last replayed"):
        silver.build_history(spark, lake)


# Bronze contract ---------------------------------------------------------------


def test_bronze_reads_cp1252_and_multiline_fields(spark, tmp_path):
    header = ",".join(EXPECTED_COLUMNS) + ","
    cells = ['"People’s Hospital;\nSecond line"'] + [""] * 18
    row = "7," + ",".join(cells) + ","
    raw = (header + "\n" + row + "\n").encode("cp1252")
    store = LocalStore(tmp_path / "data")
    land(
        store,
        source="retraction_watch",
        snapshot_date="2026-01-01",
        name="retraction_watch.csv",
        raw=raw,
        origin_url="u",
        origin_version="v",
        records=1,
        notes={"encoding": "cp1252"},
    )
    lake = Lake(root=str(tmp_path / "lake"))
    report = bronze.ingest_retraction_watch(spark, store, lake)
    assert report == [{"snapshot_date": "2026-01-01", "rows": 1, "encoding": "cp1252"}]
    title = lake.read(spark, "bronze", "retraction_watch").select("title").first()[0]
    assert title == "People’s Hospital;\nSecond line"
    assert bronze.ingest_retraction_watch(spark, store, lake) == [], "ingest is idempotent"


def test_bronze_refuses_a_row_count_mismatch(spark, tmp_path):
    header = ",".join(EXPECTED_COLUMNS) + ","
    raw = (header + "\n" + "1," + "," * 19 + "\n").encode()
    store = LocalStore(tmp_path / "data")
    land(
        store,
        source="retraction_watch",
        snapshot_date="2026-01-01",
        name="retraction_watch.csv",
        raw=raw,
        origin_url="u",
        origin_version="v",
        records=2,
    )
    with pytest.raises(bronze.ContractViolation):
        bronze.ingest_retraction_watch(spark, store, Lake(root=str(tmp_path / "lake")))
