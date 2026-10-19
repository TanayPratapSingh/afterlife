"""Bronze: landed bytes into Delta tables, one table per source, nothing inferred.

Bronze is driven by the landing manifests rather than by Auto Loader, for one
concrete reason: the encoding of a Retraction Watch snapshot is a property of
that one file (see core/text.py), and Auto Loader applies a single reader
configuration to every file in a stream. A manifest driven batch reads each
file with the encoding recorded for it. The JSON sources are UTF-8 by
specification, and on Databricks they could use Auto Loader without that
problem; they use the same batch path anyway so that the code that runs in CI
is the code that runs in production.

Every row carries lineage: which object it came from, that object's sha256,
the encoding used, and when it was ingested. Re-running bronze is a no op for
objects already ingested, checked by object key.

Contract checks run before anything is written. The row count Spark parses
must equal the count Python's csv module produced at landing time; a
disagreement means a multi line field or a quoting rule was misread, and
writing that table would corrupt every layer above it.
"""

from __future__ import annotations

import gzip
import re

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

from afterlife.core.landing import Manifest, Store, manifests
from afterlife.core.text import decode
from afterlife.spark.session import Lake, persist, release

JAVA_CHARSET = {"utf-8": "UTF-8", "cp1252": "windows-1252", "latin-1": "ISO-8859-1"}


class ContractViolation(RuntimeError):
    pass


def snake(name: str) -> str:
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", name.strip())
    return re.sub(r"[^0-9a-zA-Z]+", "_", s).strip("_").lower()


def _lineage(df: DataFrame, m: Manifest, encoding: str) -> DataFrame:
    return (
        df.withColumn("_snapshot_date", F.to_date(F.lit(m.snapshot_date)))
        .withColumn("_object_key", F.lit(m.object_key))
        .withColumn("_sha256", F.lit(m.sha256))
        .withColumn("_encoding", F.lit(encoding))
        .withColumn("_ingested_at", F.current_timestamp())
    )


def _already_ingested(spark: SparkSession, lake: Lake, table: str) -> set[str]:
    if not lake.exists(spark, "bronze", table):
        return set()
    rows = lake.read(spark, "bronze", table).select("_object_key").distinct().collect()
    return {r[0] for r in rows}


def _encoding_for(store: Store, m: Manifest) -> str:
    enc = (m.notes or {}).get("encoding")
    if enc:
        return enc
    # Manifests written before encoding was recorded: detect from the bytes.
    return decode(gzip.decompress(store.read(m.object_key))).encoding


def read_retraction_watch(
    spark: SparkSession, store: Store, m: Manifest, encoding: str | None = None
) -> DataFrame:
    encoding = encoding or _encoding_for(store, m)
    df = (
        spark.read.option("header", True)
        .option("multiLine", True)
        .option("quote", '"')
        .option("escape", '"')
        .option("encoding", JAVA_CHARSET[encoding])
        .option("mode", "FAILFAST")
        .option("inferSchema", False)
        .csv(store.uri(m.object_key))
    )
    named = [c for c in df.columns if c and not c.startswith("_c")]
    df = df.select(*[F.col(f"`{c}`").alias(snake(c)) for c in named])
    return _lineage(df, m, encoding)


def ingest_retraction_watch(spark: SparkSession, store: Store, lake: Lake) -> list[dict]:
    done = _already_ingested(spark, lake, "retraction_watch")
    report = []
    for m in sorted(manifests(store, "retraction_watch"), key=lambda x: x.snapshot_date):
        if m.object_key in done:
            continue
        encoding = _encoding_for(store, m)
        df = persist(read_retraction_watch(spark, store, m, encoding))
        n = df.count()
        if m.records is not None and n != m.records:
            raise ContractViolation(
                f"{m.object_key}: Spark parsed {n} rows, landing counted {m.records}"
            )
        _append(lake, df, "retraction_watch")
        release(df)
        report.append({"snapshot_date": m.snapshot_date, "rows": n, "encoding": encoding})
    return report


def _append(lake: Lake, df: DataFrame, table: str) -> None:
    w = df.write.format("delta").mode("append").partitionBy("_snapshot_date")
    if lake.catalog:
        w.saveAsTable(lake.name("bronze", table))
    else:
        w.save(lake.path("bronze", table))


OPENALEX_SCHEMA = T.StructType(
    [
        T.StructField("id", T.StringType()),
        T.StructField("doi", T.StringType()),
        T.StructField(
            "ids",
            T.StructType(
                [
                    T.StructField("doi", T.StringType()),
                    T.StructField("pmid", T.StringType()),
                    T.StructField("pmcid", T.StringType()),
                ]
            ),
        ),
        T.StructField("display_name", T.StringType()),
        T.StructField("publication_year", T.IntegerType()),
        T.StructField("publication_date", T.StringType()),
        T.StructField("type", T.StringType()),
        T.StructField("cited_by_count", T.LongType()),
        T.StructField(
            "counts_by_year",
            T.ArrayType(
                T.StructType(
                    [
                        T.StructField("year", T.IntegerType()),
                        T.StructField("cited_by_count", T.LongType()),
                    ]
                )
            ),
        ),
        T.StructField("is_retracted", T.BooleanType()),
        T.StructField("field", T.StringType()),
        T.StructField("domain", T.StringType()),
    ]
)


def ingest_openalex(spark: SparkSession, store: Store, lake: Lake) -> list[dict]:
    done = _already_ingested(spark, lake, "openalex_works")
    report = []
    for m in sorted(manifests(store, "openalex"), key=lambda x: x.snapshot_date):
        if m.object_key in done:
            continue
        df = (
            spark.read.schema(OPENALEX_SCHEMA)
            .option("mode", "FAILFAST")
            .json(store.uri(m.object_key))
        )
        df = persist(_lineage(df, m, "utf-8"))
        n = df.count()
        if m.records is not None and n != m.records:
            raise ContractViolation(f"{m.object_key}: parsed {n}, landed {m.records}")
        _append(lake, df, "openalex_works")
        release(df)
        report.append(
            {
                "snapshot_date": m.snapshot_date,
                "rows": n,
                "complete": (m.notes or {}).get("complete"),
            }
        )
    return report


def ingest_pubmed(spark: SparkSession, store: Store, lake: Lake) -> list[dict]:
    done = _already_ingested(spark, lake, "pubmed_flags")
    report = []
    for m in sorted(manifests(store, "pubmed"), key=lambda x: x.snapshot_date):
        if m.object_key in done:
            continue
        raw = (
            spark.read.option("multiLine", True)
            .option("mode", "FAILFAST")
            .json(store.uri(m.object_key))
        )
        df = raw.select(F.explode("pmids").alias("pmid"), F.col("term"))
        df = persist(_lineage(df, m, "utf-8"))
        n = df.count()
        if m.records is not None and n != m.records:
            raise ContractViolation(f"{m.object_key}: parsed {n}, landed {m.records}")
        _append(lake, df, "pubmed_flags")
        release(df)
        report.append({"snapshot_date": m.snapshot_date, "rows": n})
    return report


def ingest_pmc_ids(spark: SparkSession, store: Store, lake: Lake) -> list[dict]:
    """NCBI's PMC id map, 12.5 million rows. Already gzip, read as landed."""
    done = _already_ingested(spark, lake, "pmc_ids")
    report = []
    for m in sorted(manifests(store, "pmc_ids"), key=lambda x: x.snapshot_date):
        if m.object_key in done:
            continue
        df = (
            spark.read.option("header", True)
            .option("mode", "FAILFAST")
            .option("inferSchema", False)
            .csv(store.uri(m.object_key))
        )
        df = df.select(*[F.col(f"`{c}`").alias(snake(c)) for c in df.columns])
        expected = {"doi", "pmcid", "pmid", "release_date"}
        if not expected <= set(df.columns):
            raise ContractViolation(f"{m.object_key}: missing {expected - set(df.columns)}")
        df = _lineage(df, m, "utf-8")
        _append(lake, df, "pmc_ids")
        report.append(
            {
                "snapshot_date": m.snapshot_date,
                "rows": lake.read(spark, "bronze", "pmc_ids")
                .where(F.col("_object_key") == m.object_key)
                .count(),
            }
        )
    return report


PMC_METADATA_SCHEMA = T.StructType(
    [
        T.StructField("pmcid", T.StringType()),
        T.StructField("version", T.IntegerType()),
        T.StructField("pmid", T.LongType()),
        T.StructField("doi", T.StringType()),
        T.StructField("is_retracted", T.BooleanType()),
        T.StructField("license_code", T.StringType()),
        T.StructField("is_pmc_openaccess", T.BooleanType()),
        T.StructField("is_manuscript", T.BooleanType()),
        T.StructField("is_historical_ocr", T.BooleanType()),
        T.StructField("title", T.StringType()),
        T.StructField("citation", T.StringType()),
        T.StructField("in_open_data", T.BooleanType()),
        T.StructField("metadata_key", T.StringType()),
    ]
)


def ingest_pmc_metadata(spark: SparkSession, store: Store, lake: Lake) -> list[dict]:
    done = _already_ingested(spark, lake, "pmc_metadata")
    report = []
    for m in sorted(manifests(store, "pmc_metadata"), key=lambda x: x.snapshot_date):
        if m.object_key in done:
            continue
        df = spark.read.schema(PMC_METADATA_SCHEMA).option("mode", "FAILFAST")
        df = persist(_lineage(df.json(store.uri(m.object_key)), m, "utf-8"))
        n = df.count()
        if m.records is not None and n != m.records:
            raise ContractViolation(f"{m.object_key}: parsed {n}, landed {m.records}")
        _append(lake, df, "pmc_metadata")
        release(df)
        report.append({"snapshot_date": m.snapshot_date, "rows": n})
    return report


def run(spark: SparkSession, store: Store, lake: Lake) -> dict:
    return {
        "retraction_watch": ingest_retraction_watch(spark, store, lake),
        "openalex": ingest_openalex(spark, store, lake),
        "pubmed": ingest_pubmed(spark, store, lake),
        "pmc_ids": ingest_pmc_ids(spark, store, lake),
        "pmc_metadata": ingest_pmc_metadata(spark, store, lake),
    }
