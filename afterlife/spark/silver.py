"""Silver: conformed, typed, deduplicated, and historized.

The centre of this layer is `rw_history`, a slowly changing dimension over
Retraction Watch records, built by replaying every landed daily snapshot
through a Delta MERGE in date order. It is type 2 on meaning and type 1 on
bytes: a new version is cut only when a normalized value changes (SEMANTIC
below), and a change that is only formatting rewrites the current version's
raw strings in place. Without that split, one upstream export change
(2025-09-02 dropped a `+` prefix from every reason label) would have cut a new
version of 65,946 records that nobody edited.

Before any MERGE, a quality gate compares the snapshot to the current
versions. A snapshot that shows the lossy transcoding signature is recorded in
`rw_quarantine` with its evidence and not merged. From the history come:

* `rw_changes`, which columns changed on which day, for every edit after a
  record first appeared. A registry that only appends new rows never sees
  these, and they include changes to the retraction date and the DOI.
* `rw_notices`, the current version of every record, typed and normalized.
* `rw_paper_status`, one row per paper, with the status rule from
  core/status.py computed as a closed form CASE over grouped maxima.

OpenAlex and PubMed are simpler: normalize identifiers, keep the newest
snapshot, explode citation counts by year.
"""

from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

from afterlife.spark import expressions as X
from afterlife.spark.session import Lake, persist

BUSINESS = [
    "record_id",
    "title",
    "subject",
    "institution",
    "journal",
    "publisher",
    "country",
    "author",
    "urls",
    "article_type",
    "retraction_date",
    "retraction_doi",
    "retraction_pub_med_id",
    "original_paper_date",
    "original_paper_doi",
    "original_paper_pub_med_id",
    "retraction_nature",
    "reason",
    "paywalled",
    "notes",
]
SEP = "\u001f"  # unit separator; cannot occur in CSV text
NULL = "\u0000"

# What each column means, as opposed to how it was written on a given day.
# Versions in rw_history are cut when the meaning changes. The raw bytes are
# hashed too, and a raw only change is counted as format churn, not an edit.
SEMANTIC = {
    "title": X.norm_text,
    "subject": lambda c: X.norm_list(c, casefold=True),
    "institution": X.norm_text,
    "journal": X.norm_text,
    "publisher": X.norm_text,
    "country": lambda c: X.norm_list(c, casefold=True),
    "author": lambda c: X.norm_list(c, ordered=True),
    "urls": X.norm_list,
    "article_type": lambda c: X.norm_list(c, casefold=True),
    "retraction_date": lambda c: X.rw_date(c).cast("string"),
    "retraction_doi": X.doi,
    "retraction_pub_med_id": X.pmid,
    "original_paper_date": lambda c: X.rw_date(c).cast("string"),
    "original_paper_doi": X.doi,
    "original_paper_pub_med_id": X.pmid,
    "retraction_nature": X.norm_text,
    "reason": lambda c: X.norm_list(c, casefold=True),
    "paywalled": lambda c: F.lower(X.norm_text(c)),
    "notes": X.norm_text,
}
TEXT_COLUMNS = ["title", "institution", "journal", "publisher", "author", "notes"]

# A snapshot in which more records than this show the lossy transcoding
# signature is quarantined instead of merged. Set from the data, not tuned:
# the replay report lists the count for every snapshot, and every snapshot
# except 2026-10-15 shows zero (see RESULTS.md).
LOSSY_RECORDS_MAX = 25


def _hash(exprs: list[F.Column]) -> F.Column:
    return F.sha2(F.concat_ws(SEP, *[F.coalesce(e, F.lit(NULL)) for e in exprs]), 256)


def row_hash(cols: list[str]) -> F.Column:
    """sha256 over the meaning of every business column."""
    return _hash([SEMANTIC[c](F.col(c)) for c in cols])


def raw_hash(cols: list[str]) -> F.Column:
    """sha256 over the exact strings, with NULL distinct from empty."""
    return _hash([F.col(c) for c in cols])


def snapshot_rows(bronze: DataFrame, snapshot_date: str) -> tuple[DataFrame, int]:
    """One snapshot's records, keyed and hashed. Returns (rows, blank_rows).

    Snapshots from 2026-04-01 to 2026-09-01 contain lines of nothing but
    commas (32 to 285 per file, verified in the raw bytes). Bronze keeps them
    because bronze is faithful to the source. Here they are dropped and
    counted. A row with content but no Record ID is a different problem and
    still fails, as does a Record ID that appears twice.
    """
    df = bronze.where(F.col("_snapshot_date") == F.lit(snapshot_date)).select(*BUSINESS)
    blank = F.lit(True)
    for c in BUSINESS:
        blank = blank & (F.col(c).isNull() | (F.trim(F.col(c)) == ""))
    df = persist(df.withColumn("_blank", blank))
    n_blank = df.where("_blank").count()
    df = df.where(~F.col("_blank")).drop("_blank")
    orphans = df.where(F.col("record_id").isNull()).count()
    if orphans:
        raise ValueError(f"{snapshot_date}: {orphans} non blank rows have no Record ID")
    dupes = df.groupBy("record_id").count().where("count > 1").count()
    if dupes:
        raise ValueError(f"{snapshot_date}: {dupes} record ids appear more than once")
    hashed = df.withColumn("row_hash", row_hash(BUSINESS[1:])).withColumn(
        "raw_hash", raw_hash(BUSINESS[1:])
    )
    return hashed, n_blank


def _init_history(spark: SparkSession, lake: Lake, first: DataFrame, d: str) -> None:
    df = (
        first.withColumn("valid_from", F.to_date(F.lit(d)))
        .withColumn("valid_to", F.lit(None).cast("date"))
        .withColumn("is_current", F.lit(True))
        .withColumn("change_type", F.lit("insert"))
        .withColumn("end_reason", F.lit(None).cast("string"))
    )
    lake.write(
        df, "silver", "rw_history", mode="overwrite", **{"delta.enableChangeDataFeed": "true"}
    )


def quality_gate(snap: DataFrame, current: DataFrame) -> dict:
    """Evidence about one snapshot, measured against the current versions.

    `lossy_records` counts records where some text column now equals its
    previous meaning with every non cp1252 character replaced by '?'.
    `format_only` counts records whose bytes changed and meaning did not.
    """
    j = snap.alias("s").join(current.alias("c"), "record_id")
    lossy_cols = {
        c: X.lossy_transcode(X.norm_text(F.col(f"c.{c}")), X.norm_text(F.col(f"s.{c}")))
        for c in TEXT_COLUMNS
    }
    any_lossy = F.lit(False)
    for e in lossy_cols.values():
        any_lossy = any_lossy | e
    aggs = [F.sum(e.cast("int")).alias(c) for c, e in lossy_cols.items()]
    aggs += [
        F.sum(any_lossy.cast("int")).alias("_lossy"),
        F.sum(
            (
                (F.col("s.row_hash") == F.col("c.row_hash"))
                & (F.col("s.raw_hash") != F.col("c.raw_hash"))
            ).cast("int")
        ).alias("_format_only"),
    ]
    r = j.agg(*aggs).first().asDict()
    return {
        "lossy_records": int(r.pop("_lossy") or 0),
        "format_only": int(r.pop("_format_only") or 0),
        "lossy_by_column": {k: int(v or 0) for k, v in r.items() if v},
    }


def merge_snapshot(spark: SparkSession, lake: Lake, snap: DataFrame, d: str) -> dict:
    """Apply one snapshot to the history in a single MERGE, unless the
    quality gate quarantines it.

    The staged source is the classic SCD2 union: every changed or new row
    with a NULL merge key (so it can only insert), plus every snapshot row
    keyed by record_id (so it can close the version it replaces). Current
    rows absent from the snapshot are closed as deleted by
    WHEN NOT MATCHED BY SOURCE.
    """
    target = lake.delta(spark, "silver", "rw_history")
    hist = target.toDF()
    current_full = hist.where("is_current")
    gate = quality_gate(
        snap, current_full.select("record_id", "row_hash", "raw_hash", *TEXT_COLUMNS)
    )
    if gate["lossy_records"] > LOSSY_RECORDS_MAX:
        return {"snapshot_date": d, "quarantined": True, **gate}

    current = current_full.select("record_id", F.col("row_hash").alias("cur_hash"))
    ever = hist.select("record_id").distinct().withColumn("seen", F.lit(True))
    changed = (
        snap.join(current, "record_id", "left")
        .join(ever, "record_id", "left")
        .where(F.col("cur_hash").isNull() | (F.col("cur_hash") != F.col("row_hash")))
        .withColumn(
            "change_type",
            F.when(F.col("cur_hash").isNotNull(), "update")
            .when(F.col("seen"), "reappear")
            .otherwise("insert"),
        )
        .drop("cur_hash", "seen")
    )
    # Count before the MERGE. `changed` is lazy and reads the target table, so
    # evaluating it after the MERGE compares the snapshot to itself and finds
    # nothing. An earlier version of this function did exactly that.
    counts = {
        r["change_type"]: r["count"] for r in changed.groupBy("change_type").count().collect()
    }
    staged = changed.withColumn("merge_key", F.lit(None).cast("string")).unionByName(
        snap.withColumn("change_type", F.lit(None).cast("string")).withColumn(
            "merge_key", F.col("record_id")
        )
    )
    day = f"DATE'{d}'"
    insert_values = {c: f"s.{c}" for c in BUSINESS + ["row_hash", "raw_hash", "change_type"]}
    insert_values.update(
        {
            "valid_from": day,
            "valid_to": "CAST(NULL AS DATE)",
            "is_current": "true",
            "end_reason": "CAST(NULL AS STRING)",
        }
    )
    (
        target.alias("t")
        .merge(staged.alias("s"), "t.record_id = s.merge_key AND t.is_current")
        .whenMatchedUpdate(
            condition="t.row_hash <> s.row_hash",
            set={"is_current": "false", "valid_to": day, "end_reason": "'superseded'"},
        )
        # Same meaning, different bytes: refresh the raw strings in place
        # (type 1) so the next snapshot's format_only count is measured
        # against yesterday's bytes, not the bytes of the first version.
        .whenMatchedUpdate(
            condition="t.row_hash = s.row_hash AND t.raw_hash <> s.raw_hash",
            set={**{c: f"s.{c}" for c in BUSINESS[1:]}, "raw_hash": "s.raw_hash"},
        )
        .whenNotMatchedInsert(condition="s.merge_key IS NULL", values=insert_values)
        .whenNotMatchedBySourceUpdate(
            condition="t.is_current",
            set={"is_current": "false", "valid_to": day, "end_reason": "'deleted'"},
        )
        .execute()
    )
    deleted = (
        lake.read(spark, "silver", "rw_history")
        .where((F.col("end_reason") == "deleted") & (F.col("valid_to") == F.to_date(F.lit(d))))
        .count()
    )
    return {"snapshot_date": d, "quarantined": False, **counts, "deleted": deleted, **gate}


def build_history(spark: SparkSession, lake: Lake, *, rebuild: bool = False) -> list[dict]:
    """Replay snapshots not yet applied, oldest first. Idempotent."""
    bronze = lake.read(spark, "bronze", "retraction_watch")
    dates = sorted(r[0].isoformat() for r in bronze.select("_snapshot_date").distinct().collect())
    applied = set() if rebuild else _replayed_dates(spark, lake)
    exists = bool(applied) and lake.exists(spark, "silver", "rw_history")
    report = []
    for d in dates:
        if d in applied:
            continue
        if applied and d < max(applied):
            # A late arriving older snapshot cannot be merged after newer ones
            # without rewriting validity intervals. Refuse and ask for a rebuild.
            raise ValueError(
                f"snapshot {d} is older than the last replayed snapshot {max(applied)}; "
                "run with rebuild=True"
            )
        snap, blank = snapshot_rows(bronze, d)
        if not exists:
            _init_history(spark, lake, snap, d)
            report.append({"snapshot_date": d, "insert": snap.count(), "deleted": 0})
            exists = True
        else:
            report.append(merge_snapshot(spark, lake, snap, d))
        report[-1]["blank_rows_dropped"] = blank
        if report[-1].get("quarantined"):
            _record_quarantine(spark, lake, report[-1])
        _record_replay(spark, lake, d, first=not applied)
        applied.add(d)
    return report


def _record_quarantine(spark: SparkSession, lake: Lake, entry: dict) -> None:
    """Keep the decision and its evidence. The landed bytes are untouched;
    a later rebuild with a different gate can still read them."""
    import json

    df = spark.createDataFrame(
        [(entry["snapshot_date"], "lossy_transcoding", entry["lossy_records"], json.dumps(entry))],
        "snapshot_date string, reason string, lossy_records long, evidence string",
    )
    mode = "append" if lake.exists(spark, "silver", "rw_quarantine") else "overwrite"
    lake.write(df, "silver", "rw_quarantine", mode=mode)


def _replayed_dates(spark: SparkSession, lake: Lake) -> set[str]:
    if not lake.exists(spark, "silver", "rw_replay_log"):
        return set()
    return {r[0] for r in lake.read(spark, "silver", "rw_replay_log").collect()}


def _record_replay(spark: SparkSession, lake: Lake, d: str, first: bool) -> None:
    df = spark.createDataFrame([(d,)], "snapshot_date string")
    lake.write(df, "silver", "rw_replay_log", mode="overwrite" if first else "append")


def build_changes(spark: SparkSession, lake: Lake) -> DataFrame:
    """One row per version after the first: which columns differ from the
    version before it. Deletions get a row with change_type 'delete'."""
    h = lake.read(spark, "silver", "rw_history")
    w = Window.partitionBy("record_id").orderBy("valid_from")
    prev = h
    for c in BUSINESS[1:]:
        prev = prev.withColumn(f"_m_{c}", SEMANTIC[c](F.col(c)))
        prev = prev.withColumn(f"_prev_{c}", F.lag(f"_m_{c}").over(w))
    prev = prev.withColumn("_prev_from", F.lag("valid_from").over(w))
    # Compare meanings, so a version cut by a real edit to one column does not
    # also list every column whose formatting drifted at the same time.
    diffs = F.array(
        *[F.when(~F.col(f"_m_{c}").eqNullSafe(F.col(f"_prev_{c}")), F.lit(c)) for c in BUSINESS[1:]]
    )
    versions = prev.where(F.col("_prev_from").isNotNull()).select(
        "record_id",
        F.col("valid_from").alias("change_date"),
        "change_type",
        F.filter(diffs, lambda x: x.isNotNull()).alias("changed_columns"),
    )
    deletes = h.where(F.col("end_reason") == "deleted").select(
        "record_id",
        F.col("valid_to").alias("change_date"),
        F.lit("delete").alias("change_type"),
        F.array().cast("array<string>").alias("changed_columns"),
    )
    out = versions.unionByName(deletes)
    lake.write(out, "silver", "rw_changes")
    return out


def notices(h: DataFrame) -> DataFrame:
    """Typed, normalized view of history rows (current or as of a date)."""
    return h.select(
        F.col("record_id").cast("long").alias("record_id"),
        "title",
        X.semicolon_list("subject").alias("subjects"),
        "institution",
        "journal",
        "publisher",
        X.semicolon_list("country").alias("countries"),
        X.semicolon_list("author").alias("authors"),
        "urls",
        X.semicolon_list("article_type").alias("article_types"),
        X.rw_date("retraction_date").alias("notice_date"),
        X.doi("retraction_doi").alias("notice_doi"),
        X.pmid("retraction_pub_med_id").alias("notice_pmid"),
        X.rw_date("original_paper_date").alias("original_date"),
        X.doi("original_paper_doi").alias("doi"),
        X.pmid("original_paper_pub_med_id").alias("pmid"),
        F.col("retraction_nature").alias("nature"),
        X.semicolon_list("reason").alias("reasons"),
        "paywalled",
        "notes",
        "valid_from",
        "valid_to",
    ).withColumn(
        "paper_key",
        F.coalesce(
            F.concat(F.lit("doi:"), F.col("doi")),
            F.concat(F.lit("pmid:"), F.col("pmid")),
            F.concat(F.lit("rw:"), F.col("record_id").cast("string")),
        ),
    )


_RANK = {"Correction": 0, "Expression of concern": 1, "Retraction": 2, "Reinstatement": 3}


def _key(nature: str) -> F.Column:
    """core.status.order_key as a struct, so max() orders it the same way."""
    return F.when(
        F.col("nature") == nature,
        F.struct(
            F.col("notice_date").isNotNull().alias("dated"),
            # A Spark side literal, not F.lit(date.min): PySpark ships a Python
            # date through strftime("%Y-%m-%d"), which glibc renders as
            # "1-01-01" for year 1 and java.sql.Date rejects. It passed on
            # macOS and failed on Linux CI, and Databricks runs Linux.
            F.coalesce(F.col("notice_date"), F.lit("0001-01-01").cast("date")).alias("d"),
            F.lit(_RANK[nature]).alias("rank"),
        ),
    )


def paper_status(n: DataFrame) -> DataFrame:
    """core.status.closed_form, as one aggregation and one CASE."""
    g = n.groupBy("paper_key").agg(
        F.max(_key("Retraction")).alias("kr"),
        F.max(_key("Expression of concern")).alias("ke"),
        F.max(_key("Reinstatement")).alias("ki"),
        F.max(F.col("nature") == "Correction").alias("has_c"),
        F.first("doi", ignorenulls=True).alias("doi"),
        F.first("pmid", ignorenulls=True).alias("pmid"),
        F.min(F.when(F.col("nature") == "Retraction", F.col("notice_date"))).alias(
            "first_retraction_date"
        ),
        F.min("original_date").alias("original_date"),
        F.count("*").alias("n_notices"),
        F.sort_array(F.collect_set("nature")).alias("natures"),
        F.sort_array(F.collect_list("record_id")).alias("record_ids"),
        F.max(F.when(F.col("nature") == "Retraction", F.col("title"))).alias("title"),
    )
    last = F.greatest("kr", "ke", "ki")
    ok_or_corrected = F.when(F.coalesce("has_c", F.lit(False)), "corrected").otherwise("ok")
    reinstated_after_r = F.col("ki").isNotNull() & (
        F.col("kr").isNull() | (F.col("ki") > F.col("kr"))
    )
    status = (
        F.when(last.isNull(), ok_or_corrected)
        .when(last == F.col("kr"), "retracted")
        .when(
            last == F.col("ke"),
            F.when(F.col("kr").isNotNull() & ~reinstated_after_r, "retracted").otherwise("concern"),
        )
        .when(F.col("kr").isNotNull() | F.col("ke").isNotNull(), "reinstated")
        .otherwise(ok_or_corrected)
    )
    return (
        g.withColumn("status", status)
        .withColumn("last_retraction_date", F.when(F.col("kr.dated"), F.col("kr.d")))
        .drop("kr", "ke", "ki", "has_c")
    )


def build_rw(spark: SparkSession, lake: Lake) -> dict:
    h = lake.read(spark, "silver", "rw_history")
    cur = notices(h.where("is_current"))
    lake.write(cur, "silver", "rw_notices")
    status = paper_status(lake.read(spark, "silver", "rw_notices"))
    lake.write(status, "silver", "rw_paper_status")
    return {"notices": cur.count(), "papers": lake.read(spark, "silver", "rw_paper_status").count()}


def build_openalex(spark: SparkSession, lake: Lake) -> dict:
    b = lake.read(spark, "bronze", "openalex_works")
    latest = b.agg(F.max("_snapshot_date")).first()[0]
    w = b.where(F.col("_snapshot_date") == F.lit(latest))
    works = w.select(
        X.openalex_id("id").alias("openalex_id"),
        X.doi(F.coalesce(F.col("doi"), F.col("ids.doi"))).alias("doi"),
        X.pmid("ids.pmid").alias("pmid"),
        X.pmcid("ids.pmcid").alias("pmcid"),
        F.col("display_name").alias("title"),
        F.to_date("publication_date").alias("publication_date"),
        "publication_year",
        "type",
        "cited_by_count",
        "is_retracted",
        "field",
        "domain",
        "_snapshot_date",
    ).dropDuplicates(["openalex_id"])
    lake.write(works, "silver", "openalex_works")
    cites = w.select(
        X.openalex_id("id").alias("openalex_id"), F.explode("counts_by_year").alias("c")
    ).select(
        "openalex_id",
        F.col("c.year").alias("year"),
        F.col("c.cited_by_count").alias("cited_by_count"),
    )
    lake.write(cites.dropDuplicates(["openalex_id", "year"]), "silver", "openalex_citations")
    return {"works": works.count(), "snapshot_date": str(latest)}


def build_pubmed(spark: SparkSession, lake: Lake) -> dict:
    b = lake.read(spark, "bronze", "pubmed_flags")
    latest = b.agg(F.max("_snapshot_date")).first()[0]
    flags = (
        b.where(F.col("_snapshot_date") == F.lit(latest))
        .select(X.pmid("pmid").alias("pmid"), "_snapshot_date")
        .where("pmid IS NOT NULL")
        .dropDuplicates(["pmid"])
    )
    lake.write(flags, "silver", "pubmed_flags")
    return {"pmids": flags.count(), "snapshot_date": str(latest)}


def build_pmc_ids(spark: SparkSession, lake: Lake) -> dict:
    b = lake.read(spark, "bronze", "pmc_ids")
    latest = b.agg(F.max("_snapshot_date")).first()[0]
    ids = (
        b.where(F.col("_snapshot_date") == F.lit(latest))
        .select(
            X.pmcid("pmcid").alias("pmcid"),
            X.pmid("pmid").alias("pmid"),
            X.doi("doi").alias("doi"),
            "release_date",
        )
        .where("pmcid IS NOT NULL")
        .dropDuplicates(["pmcid"])
    )
    lake.write(ids, "silver", "pmc_ids")
    return {"pmcids": lake.read(spark, "silver", "pmc_ids").count(), "snapshot_date": str(latest)}


def build_pmc_targets(spark: SparkSession, lake: Lake) -> DataFrame:
    """Every PMC article any other registry mentions, and how it was linked.

    A paper reaches PMC by its PMCID directly (OpenAlex carries one), or
    through NCBI's id map by DOI or by PMID. The `via` column keeps the path,
    since a link made only by DOI is weaker evidence than one made by PMCID.
    """
    ids = lake.read(spark, "silver", "pmc_ids")
    rw = lake.read(spark, "silver", "rw_paper_status").select("doi", "pmid")
    oa = lake.read(spark, "silver", "openalex_works").select("doi", "pmid", "pmcid")
    pm = lake.read(spark, "silver", "pubmed_flags").select("pmid")
    by_doi = (
        rw.select("doi")
        .union(oa.select("doi"))
        .where("doi IS NOT NULL")
        .distinct()
        .join(ids.select("doi", "pmcid"), "doi")
        .select("pmcid", F.lit("doi").alias("via"))
    )
    by_pmid = (
        rw.select("pmid")
        .union(oa.select("pmid"))
        .union(pm)
        .where("pmid IS NOT NULL")
        .distinct()
        .join(ids.select("pmid", "pmcid"), "pmid")
        .select("pmcid", F.lit("pmid").alias("via"))
    )
    direct = oa.where("pmcid IS NOT NULL").select("pmcid", F.lit("openalex_pmcid").alias("via"))
    targets = (
        by_doi.unionByName(by_pmid)
        .unionByName(direct)
        .groupBy("pmcid")
        .agg(F.sort_array(F.collect_set("via")).alias("via"))
    )
    lake.write(targets, "silver", "pmc_targets")
    return lake.read(spark, "silver", "pmc_targets")


def build_pmc(spark: SparkSession, lake: Lake) -> dict:
    if not lake.exists(spark, "bronze", "pmc_metadata"):
        return {"skipped": "no pmc metadata landed yet"}
    b = lake.read(spark, "bronze", "pmc_metadata")
    latest = b.agg(F.max("_snapshot_date")).first()[0]
    meta = (
        b.where(F.col("_snapshot_date") == F.lit(latest))
        .select(
            X.pmcid("pmcid").alias("pmcid"),
            X.pmid(F.col("pmid").cast("string")).alias("pmid"),
            X.doi("doi").alias("doi"),
            "is_retracted",
            "in_open_data",
            "version",
            "license_code",
            "is_manuscript",
            "metadata_key",
            "_snapshot_date",
        )
        .dropDuplicates(["pmcid"])
    )
    lake.write(meta, "silver", "pmc_metadata")
    m = lake.read(spark, "silver", "pmc_metadata")
    return {
        "requested": m.count(),
        "in_open_data": m.where("in_open_data").count(),
        "flagged_retracted": m.where("is_retracted").count(),
        "snapshot_date": str(latest),
    }


def run(spark: SparkSession, lake: Lake) -> dict:
    replay = build_history(spark, lake)
    build_changes(spark, lake)
    return {
        "replay": replay,
        "rw": build_rw(spark, lake),
        "openalex": build_openalex(spark, lake),
        "pubmed": build_pubmed(spark, lake),
    }
