"""One SparkSession factory for a laptop, CI, and Databricks.

On Databricks the session already exists and tables live in Unity Catalog as
`<catalog>.<layer>.<table>`. Locally there is no metastore worth trusting, so
tables are Delta directories under `data/lake/<layer>/<table>` and are
addressed by path. `Lake` hides that difference from the jobs: they ask for
`lake.table("silver", "rw_history")` and get the right thing in both places.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def on_databricks() -> bool:
    return "DATABRICKS_RUNTIME_VERSION" in os.environ


def get_spark(app: str = "afterlife", shuffle_partitions: int = 8):
    from pyspark.sql import SparkSession

    if on_databricks():
        return SparkSession.builder.getOrCreate()

    if "JAVA_HOME" not in os.environ:
        try:  # the `local` extra ships a JRE so pip alone is enough
            import jdk4py

            os.environ["JAVA_HOME"] = str(jdk4py.JAVA_HOME)
        except ImportError:
            pass

    from delta import configure_spark_with_delta_pip

    builder = (
        SparkSession.builder.master(os.environ.get("SPARK_MASTER", "local[4]"))
        .appName(app)
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog"
        )
        .config("spark.sql.shuffle.partitions", str(shuffle_partitions))
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.driver.memory", os.environ.get("SPARK_DRIVER_MEMORY", "4g"))
        .config("spark.databricks.delta.schema.autoMerge.enabled", "false")
    )
    spark = configure_spark_with_delta_pip(builder).getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    return spark


@dataclass(frozen=True)
class Lake:
    """Where tables live. `root` for path addressed Delta, `catalog` for UC."""

    root: str | None = None
    catalog: str | None = None

    @classmethod
    def from_env(cls) -> Lake:
        if on_databricks() or os.environ.get("AFTERLIFE_CATALOG"):
            return cls(catalog=os.environ.get("AFTERLIFE_CATALOG", "afterlife"))
        return cls(root=os.environ.get("AFTERLIFE_LAKE", "data/lake"))

    def name(self, layer: str, table: str) -> str:
        """A name usable in spark.sql: catalog.schema.table or delta.`path`."""
        if self.catalog:
            return f"{self.catalog}.{layer}.{table}"
        return f"delta.`{self.path(layer, table)}`"

    def path(self, layer: str, table: str) -> str:
        if self.catalog:
            raise ValueError("Unity Catalog tables are addressed by name, not path")
        return str(Path(self.root or "data/lake").resolve() / layer / table)

    def exists(self, spark, layer: str, table: str) -> bool:
        if self.catalog:
            return spark.catalog.tableExists(f"{self.catalog}.{layer}.{table}")
        from delta.tables import DeltaTable

        return DeltaTable.isDeltaTable(spark, self.path(layer, table))

    def read(self, spark, layer: str, table: str):
        if self.catalog:
            return spark.table(self.name(layer, table))
        return spark.read.format("delta").load(self.path(layer, table))

    def delta(self, spark, layer: str, table: str):
        from delta.tables import DeltaTable

        if self.catalog:
            return DeltaTable.forName(spark, self.name(layer, table))
        return DeltaTable.forPath(spark, self.path(layer, table))

    def writer(self, df, layer: str, table: str, mode: str = "overwrite", **props):
        w = df.write.format("delta").mode(mode)
        if mode == "overwrite":
            # Silver and gold are derived. A full overwrite may change the
            # schema; bronze only ever appends, so it never takes this path.
            w = w.option("overwriteSchema", "true")
        for k, v in props.items():
            w = w.option(k, v)
        if self.catalog:
            return lambda: w.saveAsTable(self.name(layer, table))
        return lambda: w.save(self.path(layer, table))

    def write(self, df, layer: str, table: str, mode: str = "overwrite", **props) -> None:
        self.writer(df, layer, table, mode, **props)()


def persist(df):
    """`df.cache()` where caching exists. Databricks serverless compute (what
    Free Edition runs) rejects the caching APIs, and the jobs must run there
    unchanged, so a refusal falls back to recomputation."""
    try:
        return df.cache()
    except Exception:  # noqa: BLE001 - serverless raises a generic AnalysisException
        return df


def release(df) -> None:
    try:
        df.unpersist()
    except Exception:  # noqa: BLE001
        pass
