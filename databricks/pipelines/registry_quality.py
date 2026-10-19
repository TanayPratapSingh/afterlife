"""registry_quality: the publication boundary of the registry, as a Lakeflow
Declarative Pipeline (formerly Delta Live Tables).

Silver is written by the Spark jobs and gold by dbt. This pipeline reads the
two silver tables a consumer most often wants directly, `rw_notices` and
`rw_paper_status`, and publishes them into the `publication` schema only after
expectations have run, with three levels of severity:

* `paper_key_present` fails the update. A null paper key means identity
  resolution broke upstream, and publishing would silently drop papers from
  every join a consumer makes. Stopping is the only safe answer.
* `doi_shape` drops the row. silver already normalizes every DOI and nulls
  anything that is not DOI shaped, so this expectation should drop nothing. It
  is here for the day a schema change or a regression in expressions.doi lets
  a malformed value through; the event log then counts what was dropped.
* `notice_not_future_dated` (and two other checks) only warn. A retraction
  notice dated after today is an upstream data error worth seeing, not a
  reason to withhold the record.

Why a pipeline when silver already has a quality gate? The gate in silver.py
decides whether a snapshot is merged into history. Expectations decide whether
a published row is fit to serve, and the pipeline records pass and fail counts
for every expectation on every update with no code to maintain. The
`expectation_results` table keeps the same counts next to the data, queryable
with a plain SELECT and computable in a unit test.

The publication tables live in their own schema because a pipeline owns its
datasets: a full refresh or a deleted pipeline drops them, and that must never
touch the dbt marts in `gold`.

Testing: the transforms are plain functions over DataFrames and run under
pytest with a local SparkSession (tests/test_databricks.py). The `dlt`
decorators are applied only when a pipeline loads this file, where `import dlt`
succeeds. Recent runtimes also expose this API as `pyspark.pipelines`; this
file uses `dlt`, the import documented for every current pipeline runtime,
because the newer module's decorator surface was not verified here.

Every constraint is written to be NULL free (`x IS NULL OR ...`), so how the
runtime treats a NULL constraint result never decides an outcome.

The file is self contained: the pipeline does not install the afterlife wheel.
DOI_SHAPE is therefore a copy of afterlife/spark/expressions._DOI_SHAPE and
STATUSES a copy of core.status.Status, and a test requires the copies to match.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import reduce

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

try:  # present only inside a pipeline
    import dlt
except ImportError:
    dlt = None

# Copies, kept equal by tests/test_databricks.py.
DOI_SHAPE = r"(?U)^10\.[0-9]{4,9}(\.[0-9]+)*/[^\s\x{1C}-\x{1F}]+$"
STATUSES = ("ok", "corrected", "concern", "retracted", "reinstated")

ACTIONS = ("warn", "drop", "fail")
CATALOG_KEY = "afterlife.catalog"
SILVER_SCHEMA_KEY = "afterlife.silver_schema"


class ExpectationFailed(RuntimeError):
    """Raised by `enforce` where a pipeline update would fail."""


@dataclass(frozen=True)
class Expectation:
    name: str
    constraint: str
    action: str

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"{self.name}: action {self.action!r} is not one of {ACTIONS}")


def sql_string(value: str) -> str:
    """A Spark SQL string literal. Backslash is an escape character inside
    Spark SQL literals (spark.sql.parser.escapedStringLiterals is false by
    default, on Databricks as well), so a regex needs its backslashes doubled
    to reach the regex engine intact."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def doi_shape(column: str) -> str:
    return f"{column} IS NULL OR {column} RLIKE {sql_string(DOI_SHAPE)}"


PAPER_KEY_PRESENT = Expectation("paper_key_present", "paper_key IS NOT NULL", "fail")
DOI_SHAPED = Expectation("doi_shape", doi_shape("doi"), "drop")
NOT_FUTURE_DATED = Expectation(
    "notice_not_future_dated", "notice_date IS NULL OR notice_date <= current_date()", "warn"
)
STATUS_KNOWN = Expectation(
    "status_known",
    f"status IS NOT NULL AND status IN ({', '.join(sql_string(s) for s in STATUSES)})",
    "warn",
)
RETRACTION_AFTER_PUBLICATION = Expectation(
    "retraction_not_before_publication",
    "first_retraction_date IS NULL OR original_date IS NULL"
    " OR first_retraction_date >= original_date",
    "warn",
)

EXPECTATIONS: dict[str, tuple[Expectation, ...]] = {
    "notices": (PAPER_KEY_PRESENT, DOI_SHAPED, NOT_FUTURE_DATED),
    "paper_status": (
        PAPER_KEY_PRESENT,
        DOI_SHAPED,
        STATUS_KNOWN,
        RETRACTION_AFTER_PUBLICATION,
    ),
}

NOTICE_COLUMNS = (
    "paper_key",
    "record_id",
    "doi",
    "pmid",
    "notice_doi",
    "notice_pmid",
    "nature",
    "notice_date",
    "original_date",
    "title",
    "journal",
    "publisher",
    "countries",
    "subjects",
    "reasons",
    "article_types",
    "paywalled",
    "valid_from",
)
PAPER_COLUMNS = (
    "paper_key",
    "status",
    "doi",
    "pmid",
    "title",
    "n_notices",
    "natures",
    "record_ids",
    "original_date",
    "first_retraction_date",
    "last_retraction_date",
)


def published_notices(rw_notices: DataFrame) -> DataFrame:
    """The current version of every Retraction Watch notice, in the columns a
    consumer reads. Nothing is derived here: this layer adds checks, not logic,
    so a published value always equals the silver value it came from."""
    return rw_notices.select(*NOTICE_COLUMNS)


def published_paper_status(rw_paper_status: DataFrame) -> DataFrame:
    """One row per paper with its status from core/status.py."""
    return rw_paper_status.select(*PAPER_COLUMNS)


# dataset name -> (silver table, transform)
INPUTS = {
    "notices": ("rw_notices", published_notices),
    "paper_status": ("rw_paper_status", published_paper_status),
}


def enforce(df: DataFrame, expectations: Iterable[Expectation]) -> DataFrame:
    """What a pipeline update does with these expectations, for local runs and
    tests: a row failing a `fail` expectation stops the update, rows failing a
    `drop` expectation are removed, rows failing a `warn` expectation are kept.

    This is a stand in for the runtime, not the runtime. On Databricks the
    decorators at the bottom of this file are what enforce and count."""
    exps = tuple(expectations)
    for e in exps:
        if e.action == "fail" and df.where(f"NOT ({e.constraint})").limit(1).count():
            raise ExpectationFailed(f"{e.name} failed: {e.constraint}")
    for e in exps:
        if e.action == "drop":
            df = df.where(e.constraint)
    return df


def expectation_results(
    inputs: Mapping[str, DataFrame],
    expectations: Mapping[str, tuple[Expectation, ...]] = EXPECTATIONS,
) -> DataFrame:
    """One row per dataset and expectation: rows the constraint passed and
    failed, measured on the input before anything is dropped.

    One aggregation per dataset, so each input is scanned once however many
    expectations it carries. Lazy throughout: nothing is collected while the
    pipeline builds its graph."""
    parts = []
    for dataset, df in inputs.items():
        exps = expectations[dataset]
        stats = df.agg(
            F.count(F.lit(1)).alias("total_rows"),
            *[
                F.sum(F.when(F.expr(e.constraint), 0).otherwise(1)).alias(f"_failing_{i}")
                for i, e in enumerate(exps)
            ],
        )
        per_expectation = F.array(
            *[
                F.struct(
                    F.lit(e.name).alias("expectation"),
                    F.lit(e.action).alias("action"),
                    F.lit(e.constraint).alias("constraint_sql"),
                    F.coalesce(F.col(f"_failing_{i}"), F.lit(0)).cast("long").alias("failing"),
                )
                for i, e in enumerate(exps)
            ]
        )
        parts.append(
            stats.select(
                F.lit(dataset).alias("dataset"),
                F.col("total_rows").cast("long").alias("total_rows"),
                F.explode(per_expectation).alias("r"),
            ).select(
                "dataset",
                "r.expectation",
                "r.action",
                "r.constraint_sql",
                "total_rows",
                F.col("r.failing").alias("failing_rows"),
            )
        )
    out = reduce(DataFrame.unionByName, parts)
    return out.withColumn("passing_rows", F.col("total_rows") - F.col("failing_rows")).withColumn(
        "measured_at", F.current_timestamp()
    )


def _spark() -> SparkSession:
    return SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()


def _silver(table: str) -> DataFrame:
    """A silver table, by the catalog and schema the pipeline configuration
    names (databricks/resources/registry_quality.pipeline.yml)."""
    spark = _spark()
    catalog = spark.conf.get(CATALOG_KEY)
    schema = spark.conf.get(SILVER_SCHEMA_KEY, "silver")
    return spark.read.table(f"{catalog}.{schema}.{table}")


def source(dataset: str) -> DataFrame:
    table, transform = INPUTS[dataset]
    return transform(_silver(table))


# The pipeline graph. `hasattr` guards against the unrelated `dlt` package on
# PyPI (dlthub), which would otherwise be mistaken for the pipeline runtime.
if dlt is not None and hasattr(dlt, "expect_or_fail"):

    @dlt.table(
        name="notices",
        comment="Current Retraction Watch notices that passed the publication contract.",
        table_properties={"quality": "gold", "afterlife.layer": "publication"},
        cluster_by=["paper_key"],
    )
    @dlt.expect_or_fail(PAPER_KEY_PRESENT.name, PAPER_KEY_PRESENT.constraint)
    @dlt.expect_or_drop(DOI_SHAPED.name, DOI_SHAPED.constraint)
    @dlt.expect(NOT_FUTURE_DATED.name, NOT_FUTURE_DATED.constraint)
    def notices() -> DataFrame:
        return source("notices")

    @dlt.table(
        name="paper_status",
        comment="One row per paper with its Retraction Watch status, contract checked.",
        table_properties={"quality": "gold", "afterlife.layer": "publication"},
        cluster_by=["paper_key"],
    )
    @dlt.expect_or_fail(PAPER_KEY_PRESENT.name, PAPER_KEY_PRESENT.constraint)
    @dlt.expect_or_drop(DOI_SHAPED.name, DOI_SHAPED.constraint)
    @dlt.expect(STATUS_KNOWN.name, STATUS_KNOWN.constraint)
    @dlt.expect(RETRACTION_AFTER_PUBLICATION.name, RETRACTION_AFTER_PUBLICATION.constraint)
    def paper_status() -> DataFrame:
        return source("paper_status")

    @dlt.table(
        name="expectation_results",
        comment="Pass and fail counts per expectation, measured before rows are dropped.",
        table_properties={"quality": "gold", "afterlife.layer": "publication"},
    )
    def expectation_results_table() -> DataFrame:
        return expectation_results({dataset: source(dataset) for dataset in EXPECTATIONS})
