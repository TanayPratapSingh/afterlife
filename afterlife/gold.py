"""Run the dbt gold layer against the local lake and summarize the result.

`afterlife gold --target duckdb` and `--target spark` run the same models on
two engines. Paths are made absolute here because dbt resolves relative paths
against its own working directory, and a model that silently reads an empty
directory is worse than one that fails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WAREHOUSE = ROOT / "warehouse"


def dbt_env(target: str) -> dict[str, str]:
    lake = Path(os.environ.get("AFTERLIFE_LAKE", ROOT / "data" / "lake")).resolve()
    env = {**os.environ, "AFTERLIFE_LAKE_ABS": str(lake), "DBT_PROFILES_DIR": str(WAREHOUSE)}
    env.setdefault("AFTERLIFE_DUCKDB", str((ROOT / "data" / "gold" / "afterlife.duckdb").resolve()))
    Path(env["AFTERLIFE_DUCKDB"]).parent.mkdir(parents=True, exist_ok=True)
    if target == "spark" and "JAVA_HOME" not in env:
        try:
            import jdk4py

            env["JAVA_HOME"] = str(jdk4py.JAVA_HOME)
        except ImportError:
            pass
    return env


def dbt_bin() -> str:
    local = Path(sys.executable).parent / "dbt"
    return str(local) if local.exists() else (shutil.which("dbt") or "dbt")


def summarize(run_results: Path) -> dict:
    data = json.loads(run_results.read_text())
    counts: dict[str, int] = {}
    failures = []
    for r in data["results"]:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if r["status"] not in {"success", "pass"}:
            failures.append(
                {
                    "node": r["unique_id"],
                    "status": r["status"],
                    "message": (r.get("message") or "")[:300],
                }
            )
    models = [r for r in data["results"] if r["unique_id"].startswith("model.")]
    tests = [r for r in data["results"] if r["unique_id"].startswith("test.")]
    return {
        "elapsed_s": round(data.get("elapsed_time", 0), 1),
        "status_counts": counts,
        "models": len(models),
        "tests": len(tests),
        "failures": failures,
        "model_seconds": {
            r["unique_id"].split(".")[-1]: round(r["execution_time"], 2) for r in models
        },
    }


def run(target: str = "duckdb", command: str = "build", select: str | None = None) -> dict:
    cmd = [
        dbt_bin(),
        command,
        "--project-dir",
        str(WAREHOUSE),
        "--profiles-dir",
        str(WAREHOUSE),
        "--target",
        target,
    ]
    if select:
        cmd += ["--select", select]
    proc = subprocess.run(cmd, env=dbt_env(target), cwd=WAREHOUSE, capture_output=True, text=True)
    results = WAREHOUSE / "target" / "run_results.json"
    report = {
        "target": target,
        "command": " ".join(cmd[1:2] + cmd[-2:]),
        "exit_code": proc.returncode,
    }
    if results.exists():
        report.update(summarize(results))
    if proc.returncode != 0:
        report["stdout_tail"] = proc.stdout[-3000:]
    return report
