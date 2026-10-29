"""Command line entry point. Every command writes a run report to runs/.

    afterlife fetch retraction-watch --monthly --last-days 14
    afterlife fetch openalex --budget 0.09
    afterlife fetch pubmed
    afterlife bronze
    afterlife silver [--rebuild]
    afterlife gold
    afterlife export
    afterlife findings
    afterlife conformance
    afterlife results
    afterlife audit sample|sheet|score|second|agree
    afterlife fetch pubmed-notices; afterlife fetch pmc-notices; afterlife notices

The landing zone is `data/` by default, or an S3 bucket when
AFTERLIFE_LANDING=s3://bucket. The lake is `data/lake` by default, or a Unity
Catalog catalog when AFTERLIFE_CATALOG is set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

from afterlife.core.landing import LocalStore, S3Store, Store


def runs_target() -> str:
    """Where run reports go: a directory, or s3://bucket/prefix in a container
    whose root filesystem is read only (the Fargate task)."""
    return os.environ.get("AFTERLIFE_RUNS", "runs")


def store_from_env() -> Store:
    target = os.environ.get("AFTERLIFE_LANDING", "data")
    if target.startswith("s3://"):
        return S3Store(target.removeprefix("s3://").split("/", 1)[0])
    return LocalStore(target)


def write_run(command: str, report: dict, started: float) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    body = {
        "command": command,
        "argv": sys.argv[1:],
        "started_at": datetime.fromtimestamp(started, UTC).isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "seconds": round(time.time() - started, 1),
        "report": report,
    }
    data = (json.dumps(body, indent=2, default=str) + "\n").encode()
    target = runs_target()
    if target.startswith("s3://"):
        bucket, _, prefix = target.removeprefix("s3://").partition("/")
        key = f"{prefix.strip('/')}/{stamp}-{command}.json".lstrip("/")
        S3Store(bucket).write_new(key, data)
        return f"s3://{bucket}/{key}"
    path = Path(target)
    path.mkdir(parents=True, exist_ok=True)
    out = path / f"{stamp}-{command}.json"
    out.write_bytes(data)
    return str(out)


def _landed_today(store: Store, source: str) -> str | None:
    """A manifest for today's snapshot, if one exists. Checked before a fetch,
    not after: landing is idempotent anyway, but a metered OpenAlex fetch would
    already have been paid for by the time the duplicate was noticed."""
    key = f"landing/{source}/snapshot_date={date.today().isoformat()}/_manifest.json"
    return key if store.exists(key) else None


def cmd_fetch(args) -> dict:
    store = store_from_env()
    source_dir = {
        "openalex": "openalex",
        "pubmed": "pubmed",
        "pubmed-notices": "pubmed_notices",
        "pmc-notices": "pmc_notices",
    }.get(args.source)
    if source_dir and not args.force and (key := _landed_today(store, source_dir)):
        return {"skipped": "already landed today", "manifest": key}
    if args.source == "retraction-watch":
        from afterlife.sources import retraction_watch as rw

        snaps = rw.list_snapshots()
        since = date.fromisoformat(args.since) if args.since else None
        chosen = rw.select(snaps, monthly=args.monthly, last_days=args.last_days, since=since)
        landed = [
            {
                "snapshot_date": m.snapshot_date,
                "records": m.records,
                "new": new,
                "encoding": (m.notes or {}).get("encoding"),
            }
            for m, new in rw.fetch_many(store, chosen)
        ]
        return {"available": len(snaps), "chosen": len(chosen), "landed": landed}
    if args.source == "openalex":
        from afterlife.core.budget import Budget
        from afterlife.sources import openalex

        m, new = openalex.land_retracted(store, Budget(args.budget))
        return {
            "object_key": m.object_key,
            "records": m.records,
            "new": new,
            "notes": {k: v for k, v in m.notes.items() if k != "slices"},
        }
    if args.source == "pubmed":
        from afterlife.sources import pubmed

        m, new = pubmed.land_flagged(store)
        return {"object_key": m.object_key, "records": m.records, "new": new, "notes": m.notes}
    if args.source == "pubmed-notices":
        from afterlife.sources import pubmed

        landed = pubmed.land_notices(store)
        return {
            m.object_key: {"new": new, "records": m.records, "notes": m.notes} for m, new in landed
        }
    if args.source == "pmc-notices":
        from afterlife import notices

        return notices.fetch(store, workers=args.workers)
    if args.source == "pmc-ids":
        from afterlife.sources import pmc

        m, new = pmc.land_id_map(store)
        return {"object_key": m.object_key, "raw_bytes": m.raw_bytes, "new": new}
    if args.source == "pmc":
        from afterlife.sources import pmc

        if args.targets:
            ids = [x.strip() for x in Path(args.targets).read_text().split() if x.strip()]
        else:
            from afterlife.spark import silver
            from afterlife.spark.session import Lake, get_spark

            spark, lake = get_spark(), Lake.from_env()
            silver.build_pmc_ids(spark, lake)
            ids = [r[0] for r in silver.build_pmc_targets(spark, lake).select("pmcid").collect()]
        m, new = pmc.land_metadata(store, ids, workers=args.workers)
        return {"object_key": m.object_key, "records": m.records, "new": new, "notes": m.notes}
    raise SystemExit(f"unknown source {args.source}")


def cmd_bronze(args) -> dict:
    from afterlife.spark import bronze
    from afterlife.spark.session import Lake, get_spark

    return bronze.run(get_spark(), store_from_env(), Lake.from_env())


def cmd_silver(args) -> dict:
    from afterlife.spark import silver
    from afterlife.spark.session import Lake, get_spark

    spark, lake = get_spark(), Lake.from_env()
    replay = silver.build_history(spark, lake, rebuild=args.rebuild)
    silver.build_changes(spark, lake)
    return {
        "replay": replay,
        "rw": silver.build_rw(spark, lake),
        "openalex": silver.build_openalex(spark, lake),
        "pubmed": silver.build_pubmed(spark, lake),
        "pmc_ids": silver.build_pmc_ids(spark, lake),
        "pmc": silver.build_pmc(spark, lake),
    }


def cmd_gold(args) -> dict:
    from afterlife import gold

    return gold.run(target=args.target)


def cmd_export(args) -> dict:
    from afterlife import export

    return export.run(Path(args.out))


def cmd_conformance(args) -> dict:
    from afterlife import conformance

    return conformance.run(build=not args.no_build)


def cmd_results(args) -> dict:
    from afterlife import results

    return results.run(findings=args.findings)


def cmd_findings(args) -> dict:
    from afterlife import findings

    return findings.run()


def cmd_notices(args) -> dict:
    from afterlife import notices

    return notices.run(store_from_env())


def cmd_audit(args) -> dict:
    from afterlife import audit

    if args.action == "sample":
        return audit.sample(n=args.n)
    if args.action == "sheet":
        return audit.make_sheet()
    if args.action == "second":
        return audit.second_sheet()
    if args.action == "agree":
        return audit.agreement()
    return audit.run_score(sample_run=args.sample)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="afterlife")
    sub = p.add_subparsers(dest="command", required=True)

    f = sub.add_parser("fetch", help="land a source snapshot")
    f.add_argument(
        "source",
        choices=[
            "retraction-watch",
            "openalex",
            "pubmed",
            "pmc-ids",
            "pmc",
            "pubmed-notices",
            "pmc-notices",
        ],
    )
    f.add_argument("--targets", help="file of PMCIDs, one per line (default: from silver)")
    f.add_argument("--monthly", action="store_true")
    f.add_argument("--last-days", type=int, default=0)
    f.add_argument("--since")
    f.add_argument("--budget", type=float, default=0.09, help="USD cap for metered APIs")
    f.add_argument("--workers", type=int, default=16)
    f.add_argument("--force", action="store_true", help="fetch even if today is landed")
    f.set_defaults(func=cmd_fetch)

    sub.add_parser("bronze").set_defaults(func=cmd_bronze)
    s = sub.add_parser("silver")
    s.add_argument("--rebuild", action="store_true", help="replay every snapshot from scratch")
    s.set_defaults(func=cmd_silver)
    g = sub.add_parser("gold")
    g.add_argument("--target", default="duckdb")
    g.set_defaults(func=cmd_gold)
    e = sub.add_parser("export")
    e.add_argument("--out", default="data/export")
    e.set_defaults(func=cmd_export)
    sub.add_parser("findings").set_defaults(func=cmd_findings)
    sub.add_parser(
        "notices", help="how often PMC retraction notices link to what they retract"
    ).set_defaults(func=cmd_notices)
    r = sub.add_parser("results", help="render RESULTS.md from run reports")
    r.add_argument("--findings", help="a runs/*-findings.json (default: the newest)")
    r.set_defaults(func=cmd_results)
    c = sub.add_parser("conformance", help="build gold on DuckDB and Spark, compare every row")
    c.add_argument("--no-build", action="store_true")
    c.set_defaults(func=cmd_conformance)
    a = sub.add_parser("audit", help="hand audit of papers that exactly one registry flags")
    a.add_argument("action", choices=["sample", "sheet", "score", "second", "agree"])
    a.add_argument("--n", type=int, default=200)
    a.add_argument("--sample", help="a runs/*-audit-sample.json (default: the newest)")
    a.set_defaults(func=cmd_audit)

    args = p.parse_args(argv)
    started = time.time()
    suffix = getattr(args, "source", None) or getattr(args, "action", None)
    name = f"{args.command}-{suffix}" if suffix else args.command
    report = args.func(args)
    path = write_run(name, report, started)
    print(json.dumps(report, indent=2, default=str)[:4000])
    print(f"\nrun report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
