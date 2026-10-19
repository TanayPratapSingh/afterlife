"""The export task on Databricks: the files afterlife/export.py writes on a
laptop, read from Unity Catalog instead of DuckDB.

afterlife/export.py queries gold in DuckDB (`data/gold/afterlife.duckdb`) and
reads silver through DuckDB's delta_scan. Neither exists on Databricks
compute, so the job cannot call export.run. The document shape, though, is a
contract with aws/lambdas/load_registry and aws/lambdas/api, so this module
does not redefine it: it runs the same query in Spark SQL and hands every row
to afterlife.export.document, and the documents to afterlife.export.coverage.

The write half (file layout, write order, EvidenceOps lines) is
afterlife.export.write, shared with the laptop. This module supplies only the
Spark query and a parquet writer. tests/test_databricks.py runs both exports
on the same synthetic gold and requires the same bytes in every file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

DETAIL_COLUMNS = ("record_ids", "rw_natures", "reasons")


def table_names(catalog: str) -> dict[str, str]:
    """Where dbt and silver put the inputs. warehouse/macros/spark_sources.sql
    overrides generate_schema_name, so gold is `<catalog>.gold`, not
    `<catalog>.gold_gold`."""
    return {
        "registry": f"{catalog}.gold.registry",
        "identity": f"{catalog}.gold.int_paper_identity",
        "notices": f"{catalog}.silver.rw_notices",
    }


def query(registry: str, identity: str, notices: str) -> str:
    """afterlife.export.QUERY in Spark SQL.

    DuckDB's `list(distinct x order by x)` keeps NULLs where Spark's
    collect_set drops them. The only NULLs that arise here are reasons of a
    notice with none, which export.document filters out, so the documents
    agree. The parity test covers that case."""
    return f"""
with rw_detail as (
    select
        i.paper_key,
        array_sort(collect_set(n.record_id)) as record_ids,
        array_sort(collect_set(n.nature)) as natures,
        array_sort(collect_set(reason)) as reasons
    from {identity} i
    join {notices} n
      on i.source = 'retraction_watch' and i.source_key = n.paper_key
    lateral view outer explode(n.reasons) u as reason
    group by i.paper_key
)
select r.*, d.record_ids, d.natures as rw_natures, d.reasons
from {registry} r
left join rw_detail d using (paper_key)
order by r.paper_key
"""


def arrow_type(dt):
    """Spark type to Arrow type, for the types gold carries.

    pyspark.sql.pandas.types.to_arrow_schema would do this, but on Spark 3.5
    it imports distutils, which Python 3.12 no longer ships, so it fails on
    the same Python the serverless environment runs."""
    import pyarrow as pa
    from pyspark.sql import types as T

    simple = {
        T.StringType: pa.string(),
        T.LongType: pa.int64(),
        T.IntegerType: pa.int32(),
        T.ShortType: pa.int16(),
        T.BooleanType: pa.bool_(),
        T.DoubleType: pa.float64(),
        T.FloatType: pa.float32(),
        T.DateType: pa.date32(),
        T.TimestampType: pa.timestamp("us"),
        T.BinaryType: pa.binary(),
    }
    for cls, arrow in simple.items():
        if isinstance(dt, cls):
            return arrow
    if isinstance(dt, T.DecimalType):
        return pa.decimal128(dt.precision, dt.scale)
    if isinstance(dt, T.ArrayType):
        return pa.list_(arrow_type(dt.elementType))
    raise TypeError(f"no parquet mapping for {dt.simpleString()}")


def _write_parquet(df, rows: list[dict], path: Path) -> None:
    """One parquet file, as DuckDB's COPY writes it, rather than Spark's
    directory of part files. Rows are already on the driver; the schema comes
    from Spark so an all NULL column keeps its type."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema(
        [pa.field(f.name, arrow_type(f.dataType), nullable=f.nullable) for f in df.schema]
    )
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), str(path))


def run(spark, out: Path, landing_root: str, *, registry: str, identity: str, notices: str) -> dict:
    from afterlife import export

    df = spark.sql(query(registry, identity, notices))
    rows = [r.asDict(recursive=True) for r in df.collect()]
    as_of = datetime.now(UTC).date().isoformat()
    docs = [export.document(r, as_of) for r in rows]
    flat = [{k: v for k, v in r.items() if k not in DETAIL_COLUMNS} for r in rows]

    def spark_parquet(path: Path) -> None:
        _write_parquet(df.drop(*DETAIL_COLUMNS), flat, path)

    return export.write(Path(out), docs, as_of, spark_parquet, landing_root)
