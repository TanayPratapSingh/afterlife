"""The entry point every Python task of the afterlife_daily job calls.

    afterlife_job --task=bronze --catalog=afterlife \\
        --landing-root=/Volumes/afterlife/landing/files --run-id=123

A python_wheel_task runs a named entry point from an installed wheel with the
task's parameters as argv. This module is that entry point. It is separate
from afterlife/cli.py for three reasons:

* Configuration arrives as parameters, not environment variables. A job
  parameter shows on the run page and can be overridden per run
  (`databricks bundle run afterlife_daily --params fetch_enabled=true`); an
  environment variable on serverless compute can be neither.
* The run report goes to a Unity Catalog volume, /Volumes/<catalog>/ops/runs.
  A relative runs/ directory on serverless compute disappears with it.
* Exit status is the contract with the scheduler. A wheel task is judged by
  whether its entry point raises, not by what it returns, so a contract
  violation raises SystemExit(2) once the report is written. Any other
  exception propagates unchanged, traceback included, after the report is
  written.

The task bodies call the functions the CLI calls (bronze.run, silver.build_*),
so what runs in the job is what CI tests. Imports are lazy: the fetch tasks
need neither Spark nor Delta.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from afterlife import __version__
from afterlife.core.landing import LocalStore, manifests
from afterlife_jobs import ENTRY_POINT

EXIT_CONTRACT = 2

# Landing sources the silver task cannot run without, and the bronze tables
# they become. pmc_metadata is optional: silver.build_pmc skips it when absent.
REQUIRED_SOURCES = ("retraction_watch", "openalex", "pubmed", "pmc_ids")
OPTIONAL_SOURCES = ("pmc_metadata",)
REQUIRED_BRONZE = ("retraction_watch", "openalex_works", "pubmed_flags", "pmc_ids")


class ContractViolation(RuntimeError):
    """A data contract failed. Carries whatever the task measured before it
    stopped, so the run report says what was seen and not only that it failed."""

    def __init__(self, message: str, report: dict | None = None) -> None:
        super().__init__(message)
        self.report = report or {}


def _flag(value: str) -> bool:
    v = value.strip().lower()
    if v in {"true", "1", "yes"}:
        return True
    if v in {"false", "0", "no", ""}:
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, got {value!r}")


@dataclass(frozen=True)
class Context:
    task: str
    catalog: str | None
    lake_root: str | None
    landing_root: str
    runs_root: Path
    exports_root: Path
    rw_last_days: int
    silver_rebuild: bool
    run_id: str | None
    argv: tuple[str, ...]

    @classmethod
    def from_args(cls, a: argparse.Namespace, argv: list[str]) -> Context:
        if a.catalog:
            runs = a.runs_root or f"/Volumes/{a.catalog}/ops/runs"
            exports = a.exports_root or f"/Volumes/{a.catalog}/ops/exports"
        else:
            runs = a.runs_root or "runs"
            exports = a.exports_root or "data/export"
        return cls(
            task=a.task,
            catalog=a.catalog,
            lake_root=a.lake_root,
            landing_root=a.landing_root,
            runs_root=Path(runs),
            exports_root=Path(exports),
            rw_last_days=a.rw_last_days,
            silver_rebuild=a.silver_rebuild,
            run_id=a.run_id or None,
            argv=tuple(argv),
        )

    def store(self) -> LocalStore:
        # A UC volume is a POSIX path on Databricks compute, so the same
        # LocalStore that reads data/ on a laptop reads /Volumes/... here.
        return LocalStore(self.landing_root)

    def lake(self):
        from afterlife.spark.session import Lake

        return Lake(catalog=self.catalog) if self.catalog else Lake(root=self.lake_root)

    def spark(self):
        from afterlife.spark.session import get_spark, on_databricks

        if self.catalog and not on_databricks():
            # A catalog without the runtime variable: serverless compute that
            # does not set it, or Databricks Connect. Either way the session
            # already exists and must not be rebuilt with local Delta jars.
            from pyspark.sql import SparkSession

            return SparkSession.builder.getOrCreate()
        return get_spark("afterlife-job")


def _freshness(store: LocalStore, sources: tuple[str, ...]) -> dict:
    """Newest landed snapshot per source and its age in days. Reported, not
    enforced: a stale source is visible in every run report without failing
    the job on a day an upstream simply did not publish."""
    today = datetime.now(UTC).date()
    out = {}
    for s in sources:
        dates = sorted({m.snapshot_date for m in manifests(store, s)})
        newest = dates[-1] if dates else None
        out[s] = {
            "snapshots": len(dates),
            "newest": newest,
            "age_days": (today - date.fromisoformat(newest)).days if newest else None,
        }
    return out


def fetch_retraction_watch(ctx: Context) -> dict:
    from afterlife.sources import retraction_watch as rw

    snaps = rw.list_snapshots()
    if not snaps:
        raise ContractViolation("GitLab listed no Retraction Watch snapshots")
    chosen = rw.select(snaps, last_days=ctx.rw_last_days)
    landed = [
        {
            "snapshot_date": m.snapshot_date,
            "records": m.records,
            "new": new,
            "encoding": (m.notes or {}).get("encoding"),
        }
        for m, new in rw.fetch_many(ctx.store(), chosen)
    ]
    return {
        "available": len(snaps),
        "chosen": [s.snapshot_date.isoformat() for s in chosen],
        "landed": landed,
    }


def fetch_pubmed(ctx: Context) -> dict:
    from afterlife.sources import pubmed

    m, new = pubmed.land_flagged(ctx.store())
    report = {
        "object_key": m.object_key,
        "records": m.records,
        "new": new,
        "complete": (m.notes or {}).get("complete"),
    }
    if report["complete"] is False:
        # Landed and immutable, but silver keeps only the newest PubMed
        # snapshot, so letting bronze run today would shrink the flag set.
        raise ContractViolation(
            f"{m.object_key}: esearch slices do not add up to the total; "
            "not running bronze on an incomplete PubMed snapshot",
            report,
        )
    return report


def run_bronze(ctx: Context) -> dict:
    store = ctx.store()
    freshness = _freshness(store, REQUIRED_SOURCES + OPTIONAL_SOURCES)
    missing = [s for s in REQUIRED_SOURCES if not freshness[s]["snapshots"]]
    if missing:
        raise ContractViolation(
            f"landing zone {ctx.landing_root} has no manifests for {', '.join(missing)}. "
            "Land them first: databricks/README.md, section 'Load the landing zone'.",
            {"freshness": freshness},
        )
    from afterlife.spark import bronze

    try:
        report = bronze.run(ctx.spark(), store, ctx.lake())
    except bronze.ContractViolation as exc:
        raise ContractViolation(str(exc), {"freshness": freshness}) from exc
    return {**report, "freshness": freshness}


def check_silver(report: dict) -> list[str]:
    """Post conditions on the silver build. Each is cheap and each catches a
    broken join that would otherwise publish an empty or inflated registry."""
    problems = []
    rw = report["rw"]
    if not rw["notices"]:
        problems.append("silver.rw_notices is empty")
    if not rw["papers"]:
        problems.append("silver.rw_paper_status is empty")
    if rw["papers"] > rw["notices"]:
        problems.append(f"{rw['papers']} papers from {rw['notices']} notices")
    if not report["openalex"]["works"]:
        problems.append("silver.openalex_works is empty")
    if not report["pubmed"]["pmids"]:
        problems.append("silver.pubmed_flags is empty")
    if not report["pmc_ids"]["pmcids"]:
        problems.append("silver.pmc_ids is empty")
    return problems


def run_silver(ctx: Context) -> dict:
    from afterlife.spark import silver

    spark, lake = ctx.spark(), ctx.lake()
    missing = [t for t in REQUIRED_BRONZE if not lake.exists(spark, "bronze", t)]
    if missing:
        raise ContractViolation(f"bronze tables missing: {', '.join(missing)}; run bronze first")
    try:
        replay = silver.build_history(spark, lake, rebuild=ctx.silver_rebuild)
    except ValueError as exc:
        # build_history signals its data contracts with ValueError: non blank
        # rows without a Record ID, a Record ID twice in one snapshot, and a
        # snapshot older than the last one replayed.
        raise ContractViolation(str(exc)) from exc
    silver.build_changes(spark, lake)
    report = {
        "replay": replay,
        "quarantined": [r["snapshot_date"] for r in replay if r.get("quarantined")],
        "rw": silver.build_rw(spark, lake),
        "openalex": silver.build_openalex(spark, lake),
        "pubmed": silver.build_pubmed(spark, lake),
        "pmc_ids": silver.build_pmc_ids(spark, lake),
        "pmc": silver.build_pmc(spark, lake),
    }
    problems = check_silver(report)
    if problems:
        raise ContractViolation("; ".join(problems), report)
    return report


def run_export(ctx: Context) -> dict:
    if not ctx.catalog:
        raise ValueError(
            "the export task reads gold from Unity Catalog; on a laptop run `afterlife export`"
        )
    from afterlife_jobs import export

    return export.run(
        ctx.spark(),
        ctx.exports_root,
        ctx.landing_root,
        **export.table_names(ctx.catalog),
    )


TASKS: dict[str, Callable[[Context], dict]] = {
    "fetch_retraction_watch": fetch_retraction_watch,
    "fetch_pubmed": fetch_pubmed,
    "bronze": run_bronze,
    "silver": run_silver,
    "export": run_export,
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog=ENTRY_POINT, description="Run one task of the afterlife_daily Lakeflow Job."
    )
    p.add_argument("--task", required=True, choices=sorted(TASKS))
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--catalog", help="Unity Catalog catalog: tables are catalog.layer.table")
    where.add_argument("--lake-root", help="path addressed Delta lake, for local runs and tests")
    p.add_argument("--landing-root", required=True, help="the landing zone, e.g. a UC volume")
    p.add_argument("--runs-root", help="default /Volumes/<catalog>/ops/runs, or runs/")
    p.add_argument("--exports-root", help="default /Volumes/<catalog>/ops/exports")
    p.add_argument("--rw-last-days", type=int, default=3, help="newest N daily RW snapshots")
    p.add_argument("--silver-rebuild", type=_flag, default=False, help="replay from scratch")
    p.add_argument("--run-id", help="{{job.run_id}}, recorded in the report")
    return p.parse_args(argv)


def write_report(
    ctx: Context, status: str, report: dict, started: float, error: str | None = None
) -> Path:
    ctx.runs_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-run{ctx.run_id}" if ctx.run_id else ""
    path = ctx.runs_root / f"{stamp}-{ctx.task}{suffix}.json"
    body = {
        "command": ctx.task,
        "status": status,
        "error": error,
        "argv": list(ctx.argv),
        "run_id": ctx.run_id,
        "catalog": ctx.catalog,
        "lake_root": ctx.lake_root,
        "landing_root": ctx.landing_root,
        "afterlife_version": __version__,
        "started_at": datetime.fromtimestamp(started, UTC).isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "seconds": round(time.time() - started, 1),
        "report": report,
    }
    path.write_text(json.dumps(body, indent=2, default=str) + "\n")
    return path


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ctx = Context.from_args(parse_args(argv), argv)
    started = time.time()
    try:
        report = TASKS[ctx.task](ctx)
    except ContractViolation as exc:
        path = write_report(ctx, "contract_violation", exc.report, started, str(exc))
        print(f"contract violation in {ctx.task}: {exc}\nrun report: {path}", file=sys.stderr)
        raise SystemExit(EXIT_CONTRACT) from exc
    except Exception as exc:
        try:
            write_report(ctx, "error", {}, started, f"{type(exc).__name__}: {exc}")
        except OSError as report_exc:  # never mask the original failure
            print(f"could not write the run report: {report_exc}", file=sys.stderr)
        raise
    path = write_report(ctx, "succeeded", report, started)
    print(json.dumps(report, indent=2, default=str)[:4000])
    print(f"\nrun report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
