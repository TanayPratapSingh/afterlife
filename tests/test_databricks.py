"""The Databricks layer, checked without a workspace.

* Static: every YAML file in databricks/ parses, every path it references
  exists, every depends_on names a task, the job DAG is acyclic and fits Free
  Edition's concurrency limit, compute is bound exactly once per target, and
  the entry point the job calls is the one afterlife_jobs defines. When the
  Databricks CLI is in .tools/, the bundle is also checked against the CLI's
  own JSON schema.
* Spark (marked `spark`): the registry_quality transforms and expectations,
  and the job entry point end to end (landing, bronze, silver, publication) on
  a tiny synthetic lake under tmp_path.
* Parity: the Databricks export against afterlife/export.py on the same
  synthetic gold, file by file.

None of this is a deploy. databricks/README.md lists what ran where.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import importlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tomllib
import types
import warnings
from copy import deepcopy
from datetime import date
from itertools import combinations
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "databricks"
SRC = BUNDLE / "src"
PIPELINE = BUNDLE / "pipelines" / "registry_quality.py"
SETUP_SQL = BUNDLE / "sql" / "setup.sql"
NOTEBOOK = BUNDLE / "notebooks" / "explore_registry.sql"
JOB = "afterlife_daily"
TARGETS = ("free", "aws")
DASHES = {chr(0x2014), chr(0x2013)}  # em and en dash, spelled so this file has neither
KEYED_LISTS = {
    "tasks": "task_key",
    "job_clusters": "job_cluster_key",
    "environments": "environment_key",
}


# helpers ------------------------------------------------------------------


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def root_config() -> dict:
    return load(BUNDLE / "databricks.yml")


def config_files() -> list[Path]:
    files = [BUNDLE / "databricks.yml"]
    for pattern in root_config()["include"]:
        files += sorted(BUNDLE.glob(pattern))
    return files


def merge(base: dict, override: dict) -> dict:
    """The bundle's override rule: maps merge, keyed lists merge by key."""
    out = dict(base)
    for k, v in override.items():
        if k in KEYED_LISTS and isinstance(out.get(k), list):
            key = KEYED_LISTS[k]
            by_key = {item[key]: dict(item) for item in out[k]}
            for item in v:
                by_key[item[key]] = merge(by_key.get(item[key], {}), item)
            out[k] = list(by_key.values())
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out


def base_job() -> tuple[Path, dict]:
    found = [
        (f, load(f)["resources"]["jobs"][JOB])
        for f in config_files()
        if JOB in (load(f).get("resources") or {}).get("jobs", {})
    ]
    assert len(found) == 1, f"{JOB} must be defined in exactly one file"
    return found[0]


def job_overrides(target: str) -> list[dict]:
    out = []
    for f in config_files():
        t = (load(f).get("targets") or {}).get(target) or {}
        job = ((t.get("resources") or {}).get("jobs") or {}).get(JOB)
        if job:
            out.append(job)
    return out


def merged_job(target: str) -> dict:
    job = deepcopy(base_job()[1])
    for override in job_overrides(target):
        job = merge(job, override)
    return job


def tasks_by_key(job: dict) -> dict[str, dict]:
    return {t["task_key"]: t for t in job["tasks"]}


def edges(job: dict) -> dict[str, set[str]]:
    return {t["task_key"]: {d["task_key"] for d in t.get("depends_on", [])} for t in job["tasks"]}


def walk(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, (*path, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, (*path, i))
    else:
        yield path, node


def variable_defaults() -> dict[str, str]:
    out = {}
    for name, spec in root_config()["variables"].items():
        out[name] = str(spec.get("default", f"<lookup:{name}>"))
    return out


def resolve(value: str, job: dict) -> str:
    """Stand in for bundle interpolation and job parameter substitution."""
    params = {p["name"]: str(p["default"]) for p in job["parameters"]}
    value = re.sub(r"\{\{job\.parameters\.(\w+)\}\}", lambda m: params[m.group(1)], value)
    value = value.replace("{{job.run_id}}", "123")
    return re.sub(r"\$\{var\.(\w+)\}", lambda m: variable_defaults()[m.group(1)], value)


def load_pipeline(dlt_module=None):
    """Import the pipeline file under a fresh module name, with `dlt` either
    absent (as on a laptop) or replaced by a stub."""
    pytest.importorskip("pyspark")
    saved = sys.modules.get("dlt", "<absent>")
    sys.modules["dlt"] = dlt_module  # None makes `import dlt` raise ImportError
    try:
        name = f"registry_quality_{id(dlt_module)}"
        spec = importlib.util.spec_from_file_location(name, PIPELINE)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod  # dataclasses resolve annotations through sys.modules
        spec.loader.exec_module(mod)
    finally:
        if saved == "<absent>":
            del sys.modules["dlt"]
        else:
            sys.modules["dlt"] = saved
    return mod


@pytest.fixture(scope="module")
def jobs():
    """afterlife_jobs.main, importable from databricks/src before the
    pyproject.toml change packages it."""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    return importlib.import_module("afterlife_jobs.main")


@pytest.fixture(scope="module")
def pipeline():
    return load_pipeline(None)


def reports(runs: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(runs.glob("*.json"))]


# static: the bundle ------------------------------------------------------------


def test_every_yaml_file_parses():
    files = sorted(BUNDLE.rglob("*.yml")) + sorted(BUNDLE.rglob("*.yaml"))
    assert len(files) >= 3
    for f in files:
        assert isinstance(load(f), dict), f


def test_bundle_has_both_targets_and_the_declared_variables():
    cfg = root_config()
    assert cfg["bundle"]["name"] == "afterlife"
    assert {"catalog", "landing_root", "warehouse_id", "notification_email"} <= set(
        cfg["variables"]
    )
    targets = cfg["targets"]
    assert set(TARGETS) <= set(targets)
    assert targets["free"]["mode"] == "development" and targets["free"]["default"] is True
    assert targets["aws"]["mode"] == "production"
    assert "service_principal_name" in targets["aws"]["run_as"]
    assert targets["aws"]["permissions"]
    # Only the free target is the default, so a bare deploy never hits AWS.
    assert [t for t, spec in targets.items() if spec.get("default")] == ["free"]
    for f in config_files():
        for t in load(f).get("targets") or {}:
            assert t in targets, f"{f.name} overrides undeclared target {t}"


def test_include_globs_match_resource_files():
    included = config_files()[1:]
    names = {f.name for f in included}
    assert {"afterlife_daily.job.yml", "registry_quality.pipeline.yml"} <= names


def test_artifact_builds_the_project_wheel_from_the_repository_root():
    artifacts = root_config()["artifacts"]
    assert len(artifacts) == 1
    (art,) = artifacts.values()
    assert art["type"] == "whl"
    assert (BUNDLE / art["path"]).resolve() == ROOT
    assert (ROOT / "pyproject.toml").exists()
    assert "build" in art["build"]


def test_every_variable_reference_is_declared():
    declared = set(root_config()["variables"])
    for f in config_files():
        text = f.read_text()
        for name in re.findall(r"\$\{var\.([A-Za-z_][A-Za-z0-9_]*)", text):
            assert name in declared, f"{f.name}: ${{var.{name}}} is not declared"
        for spec in (load(f).get("targets") or {}).values():
            for name in (spec or {}).get("variables") or {}:
                assert name in declared, f"{f.name}: target overrides undeclared {name}"


def test_resource_references_point_at_defined_resources():
    defined = {
        (kind, key)
        for f in config_files()
        for kind, items in (load(f).get("resources") or {}).items()
        for key in items
    }
    for f in config_files():
        for kind, key in re.findall(r"\$\{resources\.(\w+)\.(\w+)\.", f.read_text()):
            assert (kind, key) in defined, f"{f.name}: resources.{kind}.{key} is not defined"


def test_every_referenced_path_exists():
    """Bundle paths are relative to the file that names them."""
    checked = 0
    dist = (ROOT / "dist").resolve()
    for f in config_files():
        for path, value in walk(load(f)):
            if not isinstance(value, str):
                continue
            key = path[-1]
            parent = path[-2] if len(path) > 1 else None
            local = None
            if key == "path" and parent == "file":  # sql_task.file, pipeline file library
                local = value
            elif key == "project_directory":
                local = value
            elif key == "whl" or (parent == "dependencies" and value.startswith(".")):
                # Built by the artifact before upload; check the directory it lands in.
                assert value.endswith("*.whl"), value
                assert (f.parent / value).parent.resolve() == dist, f"{f.name}: {value}"
                checked += 1
                continue
            if local is not None:
                assert (f.parent / local).exists(), f"{f.name}: {'.'.join(map(str, path))}"
                checked += 1
    for p in root_config()["sync"]["paths"]:
        assert (BUNDLE / p).is_dir(), p
    assert checked >= 5


def test_dbt_project_is_present():
    project = (
        BUNDLE / "resources" / tasks_by_key(base_job()[1])["gold"]["dbt_task"]["project_directory"]
    ).resolve()
    assert project == ROOT / "warehouse"
    if not (project / "dbt_project.yml").exists():
        pytest.skip("warehouse/dbt_project.yml not written yet")
    assert load(project / "dbt_project.yml")["profile"] == "afterlife"


# static: the job ----------------------------------------------------------------


@pytest.mark.parametrize("target", TARGETS)
def test_depends_on_refers_to_defined_tasks(target):
    job = merged_job(target)
    tasks = tasks_by_key(job)
    assert len(tasks) == len(job["tasks"]), "duplicate task_key"
    for t in job["tasks"]:
        for d in t.get("depends_on", []):
            assert d["task_key"] in tasks, f"{t['task_key']} depends on unknown {d['task_key']}"
            if "outcome" in d:
                assert "condition_task" in tasks[d["task_key"]], "outcome needs a condition task"


@pytest.mark.parametrize("target", TARGETS)
def test_job_dag_has_no_cycles(target):
    deps = {k: set(v) for k, v in edges(merged_job(target)).items()}
    order = []
    ready = sorted(k for k, v in deps.items() if not v)
    while ready:
        n = ready.pop()
        order.append(n)
        for k, v in deps.items():
            if n in v:
                v.discard(n)
                if not v and k not in order and k not in ready:
                    ready.append(k)
    assert len(order) == len(deps), f"cycle among {set(deps) - set(order)}"


def test_job_concurrency_fits_free_edition():
    """Free Edition runs at most 5 job tasks at once per account. The widest
    set of tasks that could run together is the largest antichain."""
    deps = edges(merged_job("free"))

    def ancestors(n, seen=None):
        seen = set() if seen is None else seen
        for d in deps[n]:
            if d not in seen:
                seen.add(d)
                ancestors(d, seen)
        return seen

    up = {n: ancestors(n) for n in deps}
    width = max(
        len(s)
        for r in range(1, len(deps) + 1)
        for s in combinations(deps, r)
        if all(a not in up[b] and b not in up[a] for a, b in combinations(s, 2))
    )
    assert width <= 5
    assert width == 2, "update the concurrency note at the top of afterlife_daily.job.yml"


def test_target_overrides_only_add_compute():
    """An override can add a key but not remove one, so overrides must name
    existing tasks and never restate a key the shared definition already has."""
    base = tasks_by_key(base_job()[1])
    for target in TARGETS:
        for override in job_overrides(target):
            for t in override.get("tasks", []):
                assert t["task_key"] in base, f"{target} adds a partial task {t['task_key']}"
                clash = (set(t) - {"task_key"}) & set(base[t["task_key"]])
                assert not clash, f"{target} restates {clash} on {t['task_key']}"


def test_compute_is_bound_once_per_target():
    compute_kinds = ("python_wheel_task", "dbt_task")
    free = merged_job("free")
    envs = {e["environment_key"]: e for e in free["environments"]}
    for t in free["tasks"]:
        if any(k in t for k in compute_kinds):
            assert t.get("environment_key") in envs, t["task_key"]
            assert "job_cluster_key" not in t and "libraries" not in t, t["task_key"]
        else:
            assert "environment_key" not in t, t["task_key"]
    wheel_env = envs[tasks_by_key(free)["bronze"]["environment_key"]]
    assert any(d.endswith(".whl") for d in wheel_env["spec"]["dependencies"])
    assert all("environment_version" in e["spec"] for e in envs.values())
    assert "job_clusters" not in free

    aws = merged_job("aws")
    clusters = {c["job_cluster_key"]: c["new_cluster"] for c in aws["job_clusters"]}
    assert "environments" not in aws
    for t in aws["tasks"]:
        if any(k in t for k in compute_kinds):
            assert t.get("job_cluster_key") in clusters, t["task_key"]
            assert t["libraries"], t["task_key"]
            assert "environment_key" not in t, t["task_key"]
    for c in clusters.values():
        assert c["spark_version"].startswith("15.4.")


def test_wheel_tasks_call_the_entry_point_module(jobs):
    from afterlife_jobs import ENTRY_POINT, PACKAGE

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    job = merged_job("free")
    wheel_tasks = [t for t in job["tasks"] if "python_wheel_task" in t]
    assert len(wheel_tasks) == 5
    for t in wheel_tasks:
        w = t["python_wheel_task"]
        assert w["package_name"] == PACKAGE == project["name"]
        assert w["entry_point"] == ENTRY_POINT
        argv = [resolve(p, job) for p in w["parameters"]]
        args = jobs.parse_args(argv)  # the exact argv the job passes must parse
        assert args.task == t["task_key"] and args.task in jobs.TASKS
        assert args.catalog == "afterlife" and args.run_id == "123"


def test_pyproject_packages_the_entry_point():
    from_py = tomllib.loads((ROOT / "pyproject.toml").read_text())
    scripts = from_py["project"].get("scripts", {})
    if "afterlife_job" not in scripts:
        pytest.skip("pending: pyproject.toml change described in databricks/README.md")
    assert scripts["afterlife_job"] == "afterlife_jobs.main:main"
    where = from_py["tool"]["setuptools"]["packages"]["find"]["where"]
    assert "databricks/src" in where


def test_fetch_gate_skips_fetch_without_skipping_bronze():
    job = merged_job("free")
    tasks = tasks_by_key(job)
    gate = tasks["fetch_gate"]["condition_task"]
    assert gate == {"op": "EQUAL_TO", "left": "{{job.parameters.fetch_enabled}}", "right": "true"}
    for name in ("fetch_retraction_watch", "fetch_pubmed"):
        assert tasks[name]["depends_on"] == [{"task_key": "fetch_gate", "outcome": "true"}]
    bronze = tasks["bronze"]
    # NONE_FAILED: runs when the fetches were excluded (setup still ran), and
    # does not run when a fetch failed.
    assert bronze["run_if"] == "NONE_FAILED"
    assert {d["task_key"] for d in bronze["depends_on"]} == {
        "setup",
        "fetch_retraction_watch",
        "fetch_pubmed",
    }
    assert variable_defaults()["fetch_enabled"] in {"true", "false"}


def test_schedule_retries_timeouts_and_notifications():
    job = merged_job("free")
    sec, minute, hour, *_ = job["schedule"]["quartz_cron_expression"].split()
    assert job["schedule"]["timezone_id"] == "UTC"
    assert (int(hour), int(minute)) >= (23, 30), "upstream publishes at 23:00 UTC"
    assert job["max_concurrent_runs"] == 1
    assert job["email_notifications"]["on_failure"] == ["${var.notification_email}"]
    assert job["tags"]["project"] == "afterlife"
    for t in job["tasks"]:
        if "condition_task" in t:
            continue
        assert t["timeout_seconds"] > 0, t["task_key"]
        if "python_wheel_task" in t or "dbt_task" in t:
            assert t.get("max_retries", 0) >= 1, t["task_key"]


def test_job_parameter_references_are_defined():
    job = merged_job("free")
    names = {p["name"] for p in job["parameters"]}
    used = set(re.findall(r"\{\{job\.parameters\.(\w+)\}\}", json.dumps(job)))
    assert used <= names, used - names
    assert {"catalog", "landing_root", "fetch_enabled"} <= used


def test_sql_task_parameter_matches_the_marker_in_setup_sql():
    setup = tasks_by_key(merged_job("free"))["setup"]["sql_task"]
    assert set(setup["parameters"]) == {"catalog"}
    sql = "\n".join(_statements(SETUP_SQL))
    assert "${" not in sql, "bundles do not interpolate inside files"
    assert set(re.findall(r":(\w+)\)", sql)) == {"catalog"}


# static: SQL and the notebook -------------------------------------------------


def _statements(path: Path) -> list[str]:
    body = "\n".join(line for line in path.read_text().splitlines() if not line.startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def created(kind: str) -> set[str]:
    pattern = rf"CREATE {kind} IF NOT EXISTS (\w+(?:\.\w+)?)"
    return {m for s in _statements(SETUP_SQL) for m in re.findall(pattern, s)}


def test_setup_sql_creates_every_schema_and_volume_the_bundle_uses():
    schemas = created("SCHEMA")
    assert {"landing", "bronze", "silver", "gold", "ops", "publication"} <= schemas
    assert {"landing.files", "ops.runs", "ops.exports"} <= created("VOLUME")
    stmts = _statements(SETUP_SQL)
    assert stmts[0].startswith("CREATE CATALOG IF NOT EXISTS IDENTIFIER(:catalog)")
    assert stmts[1] == "USE CATALOG IDENTIFIER(:catalog)"
    assert all("COMMENT" in s for s in stmts if s.startswith("CREATE"))
    # Schemas named in YAML exist.
    pipeline = load(BUNDLE / "resources" / "registry_quality.pipeline.yml")
    spec = pipeline["resources"]["pipelines"]["registry_quality"]
    assert spec["schema"] in schemas and spec["event_log"]["schema"] in schemas
    assert tasks_by_key(base_job()[1])["gold"]["dbt_task"]["schema"] in schemas
    # Volume paths the bundle defaults point at exist.
    defaults = variable_defaults()
    for var in ("landing_root", "exports_root"):
        _, _, catalog, schema, volume, *_ = defaults[var].split("/")
        assert catalog == defaults["catalog"] and f"{schema}.{volume}" in created("VOLUME")


def test_notebook_is_databricks_source_format_and_reads_created_schemas():
    text = NOTEBOOK.read_text()
    assert text.splitlines()[0] == "-- Databricks notebook source"
    assert text.count("-- COMMAND ----------") >= 10
    used = set(re.findall(r"\b(?:FROM|IN|HISTORY)\s+(gold|silver|publication|ops|bronze)\b", text))
    used |= set(re.findall(r"\bFROM\s+(\w+)\.\w+", text))
    assert used <= created("SCHEMA"), used - created("SCHEMA")


def test_no_em_or_en_dashes_in_the_databricks_layer():
    files = [p for p in BUNDLE.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    for p in [*files, Path(__file__)]:
        text = p.read_text(errors="replace")
        assert not DASHES & set(text), p


def test_bundle_matches_the_cli_json_schema():
    """Optional: needs the Databricks CLI in .tools/ (see databricks/README.md).
    `databricks bundle schema` is offline; nothing here talks to a workspace."""
    cli = ROOT / ".tools" / "databricks"
    if not cli.exists():
        pytest.skip("Databricks CLI not installed in .tools/")
    jsonschema = pytest.importorskip("jsonschema")
    env = {k: v for k, v in os.environ.items() if not k.startswith("DATABRICKS_")}
    out = subprocess.run(
        [str(cli), "bundle", "schema"], capture_output=True, text=True, check=True, env=env
    ).stdout

    def python_patterns(node):  # Go regexp classes the `re` module lacks
        if isinstance(node, dict):
            return {
                k: v.replace(r"\p{L}", r"[^\W\d_]").replace(r"\p{N}", r"\d")
                if k == "pattern" and isinstance(v, str)
                else python_patterns(v)
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [python_patterns(x) for x in node]
        return node

    schema = python_patterns(json.loads(out))
    validator = jsonschema.Draft7Validator(schema)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        for f in config_files():
            errors = [e.message for e in validator.iter_errors(load(f))]
            assert not errors, f"{f.name}: {errors[:3]}"


# static: the pipeline -------------------------------------------------------


def test_pipeline_copies_match_their_sources(pipeline):
    from afterlife.core.status import Status
    from afterlife.spark import expressions

    assert pipeline.DOI_SHAPE == expressions._DOI_SHAPE
    assert set(pipeline.STATUSES) == {s.value for s in Status}


def test_pipeline_resource(pipeline):
    spec = load(BUNDLE / "resources" / "registry_quality.pipeline.yml")["resources"]["pipelines"][
        "registry_quality"
    ]
    assert spec["serverless"] is True
    assert spec["catalog"] == "${var.catalog}"
    assert set(spec["configuration"]) == {pipeline.CATALOG_KEY, pipeline.SILVER_SCHEMA_KEY}
    (lib,) = spec["libraries"]
    assert (BUNDLE / "resources" / lib["file"]["path"]).resolve() == PIPELINE


class RecordingDlt(types.ModuleType):
    """Just enough of the `dlt` module to record what the pipeline declares."""

    def __init__(self) -> None:
        super().__init__("dlt")
        self.tables: dict[str, dict] = {}
        self.expect = self._expectation("warn")
        self.expect_or_drop = self._expectation("drop")
        self.expect_or_fail = self._expectation("fail")

    @staticmethod
    def _expectation(action: str):
        def factory(name: str, constraint: str):
            def deco(fn):
                fn.__dict__.setdefault("_expectations", set()).add((name, constraint, action))
                return fn

            return deco

        return factory

    def table(self, **kwargs):
        def deco(fn):
            self.tables[kwargs["name"]] = {
                "fn": fn,
                "kwargs": kwargs,
                "expectations": fn.__dict__.get("_expectations", set()),
            }
            return fn

        return deco


def test_pipeline_declares_exactly_the_registered_expectations():
    stub = RecordingDlt()
    mod = load_pipeline(stub)
    assert set(stub.tables) == {"notices", "paper_status", "expectation_results"}
    for dataset, exps in mod.EXPECTATIONS.items():
        declared = stub.tables[dataset]["expectations"]
        assert declared == {(e.name, e.constraint, e.action) for e in exps}, dataset
    assert not stub.tables["expectation_results"]["expectations"]
    assert {e.action for e in mod.EXPECTATIONS["notices"]} == {"fail", "drop", "warn"}


def test_pipeline_ignores_an_unrelated_dlt_package():
    impostor = types.ModuleType("dlt")  # dlthub's dlt has no expect_or_fail
    mod = load_pipeline(impostor)
    assert hasattr(mod, "published_notices") and not hasattr(mod, "expectation_results_table")


# entry point, no Spark ------------------------------------------------------


def test_flag_parsing(jobs):
    assert jobs._flag("TRUE") is True and jobs._flag("false") is False
    with pytest.raises(argparse.ArgumentTypeError):
        jobs._flag("maybe")


def test_fetch_retraction_watch_dispatch(jobs, tmp_path, monkeypatch):
    from afterlife.core.landing import Manifest
    from afterlife.sources import retraction_watch as rw

    snaps = [types.SimpleNamespace(snapshot_date=date(2026, 1, d)) for d in range(1, 6)]
    seen = {}

    def fake_fetch_many(store, chosen):
        seen["chosen"] = [s.snapshot_date for s in chosen]
        seen["root"] = store.root
        yield (
            Manifest(
                source="retraction_watch",
                snapshot_date="2026-01-05",
                origin_url="synthetic",
                origin_version="v",
                sha256="0" * 64,
                raw_bytes=1,
                stored_bytes=1,
                object_key="landing/retraction_watch/snapshot_date=2026-01-05/x.gz",
                records=7,
                notes={"encoding": "utf-8"},
            ),
            True,
        )

    monkeypatch.setattr(rw, "list_snapshots", lambda: snaps)
    monkeypatch.setattr(rw, "fetch_many", fake_fetch_many)
    runs = tmp_path / "runs"
    code = jobs.main(
        [
            "--task=fetch_retraction_watch",
            "--catalog=afterlife",
            f"--landing-root={tmp_path / 'landing'}",
            f"--runs-root={runs}",
            "--rw-last-days=2",
            "--run-id=42",
        ]
    )
    assert code == 0
    assert seen["chosen"] == [date(2026, 1, 4), date(2026, 1, 5)]
    assert seen["root"] == tmp_path / "landing"
    (report,) = reports(runs)
    assert report["status"] == "succeeded" and report["run_id"] == "42"
    assert report["report"]["landed"][0]["records"] == 7


def test_incomplete_pubmed_snapshot_fails_the_task(jobs, tmp_path, monkeypatch):
    from afterlife.core.landing import Manifest
    from afterlife.sources import pubmed

    m = Manifest(
        source="pubmed",
        snapshot_date="2026-01-05",
        origin_url="synthetic",
        origin_version="esearch",
        sha256="0" * 64,
        raw_bytes=1,
        stored_bytes=1,
        object_key="landing/pubmed/snapshot_date=2026-01-05/x.gz",
        records=3,
        notes={"complete": False},
    )
    monkeypatch.setattr(pubmed, "land_flagged", lambda store: (m, True))
    runs = tmp_path / "runs"
    with pytest.raises(SystemExit) as exc:
        jobs.main(
            [
                "--task=fetch_pubmed",
                "--catalog=x",
                f"--landing-root={tmp_path}",
                f"--runs-root={runs}",
            ]
        )
    assert exc.value.code == jobs.EXIT_CONTRACT
    (report,) = reports(runs)
    assert report["status"] == "contract_violation"
    assert report["report"]["complete"] is False


def test_bronze_refuses_an_empty_landing_zone_before_starting_spark(jobs, tmp_path):
    runs = tmp_path / "runs"
    with pytest.raises(SystemExit) as exc:
        jobs.main(
            [
                "--task=bronze",
                f"--lake-root={tmp_path / 'lake'}",
                f"--landing-root={tmp_path / 'empty'}",
                f"--runs-root={runs}",
            ]
        )
    assert exc.value.code == jobs.EXIT_CONTRACT
    (report,) = reports(runs)
    assert "no manifests for retraction_watch, openalex, pubmed, pmc_ids" in report["error"]
    assert report["report"]["freshness"]["retraction_watch"]["snapshots"] == 0


def test_export_needs_unity_catalog_and_reports_the_error(jobs, tmp_path):
    runs = tmp_path / "runs"
    with pytest.raises(ValueError, match="afterlife export"):
        jobs.main(
            [
                "--task=export",
                f"--lake-root={tmp_path / 'lake'}",
                f"--landing-root={tmp_path}",
                f"--runs-root={runs}",
            ]
        )
    (report,) = reports(runs)
    assert report["status"] == "error" and report["error"].startswith("ValueError")


# Spark ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spark():
    pytest.importorskip("pyspark")
    pytest.importorskip("delta")
    from afterlife.spark.session import get_spark

    try:
        session = get_spark("afterlife-databricks-tests", shuffle_partitions=2)
    except Exception as exc:  # noqa: BLE001 - no Java runtime, or no Delta jars
        pytest.skip(f"local Spark unavailable: {exc}")
    yield session
    session.stop()


def notice_rows(spark, rows: list[tuple]):
    return spark.createDataFrame(
        rows, "paper_key string, doi string, notice_date date, record_id long"
    )


@pytest.mark.spark
def test_doi_constraint_agrees_with_the_python_rule(spark, pipeline):
    from pyspark.sql import functions as F

    from afterlife.core import ids

    values = [
        "10.1000/abc",
        "10.1000.10/abc",
        "10.1061/(asce)0733-9399(2010)136:2(174)",
        "10.12/abc",
        "10.1000/",
        "10.1000/a b",
        "11.1000/abc",
        "doi:10.1000/abc",
        "10.1000/x'y",
        "10.1000/back\\slash",
        None,
    ]
    df = spark.createDataFrame([(v,) for v in values], "doi string")
    got = dict(df.select("doi", F.expr(pipeline.DOI_SHAPED.constraint).alias("ok")).collect())
    for v in values:
        assert got[v] == (v is None or bool(ids.DOI_SHAPE.match(v))), v


@pytest.mark.spark
def test_enforce_drops_warns_and_fails_like_the_pipeline(spark, pipeline):
    rows = [
        ("doi:10.1000/a", "10.1000/a", date(2020, 1, 1), 1),
        ("doi:10.12/bad", "10.12/bad", date(2020, 1, 1), 2),  # drop
        ("doi:10.1000/c", "10.1000/c", date(2999, 1, 1), 3),  # warn, kept
        ("rw:4", None, None, 4),
    ]
    exps = pipeline.EXPECTATIONS["notices"]
    kept = pipeline.enforce(notice_rows(spark, rows), exps)
    assert sorted(r.record_id for r in kept.collect()) == [1, 3, 4]

    bad = notice_rows(spark, [*rows, (None, "10.1000/e", None, 5)])
    with pytest.raises(pipeline.ExpectationFailed, match="paper_key_present"):
        pipeline.enforce(bad, exps)


@pytest.mark.spark
def test_expectation_results_counts_before_dropping(spark, pipeline):
    rows = [
        ("doi:10.1000/a", "10.1000/a", date(2020, 1, 1), 1),
        ("doi:10.12/bad", "10.12/bad", date(2999, 1, 1), 2),
        (None, None, None, 3),
    ]
    status = spark.createDataFrame(
        [("doi:10.1000/a", "retracted", "10.1000/a", date(2019, 1, 1), date(2018, 1, 1))],
        "paper_key string, status string, doi string, original_date date, "
        "first_retraction_date date",
    )
    inputs = {"notices": notice_rows(spark, rows), "paper_status": status}
    out = pipeline.expectation_results(inputs)
    got = {(r.dataset, r.expectation): r for r in out.collect()}
    assert len(got) == sum(len(v) for v in pipeline.EXPECTATIONS.values())
    assert got["notices", "paper_key_present"].failing_rows == 1
    assert got["notices", "doi_shape"].failing_rows == 1
    assert got["notices", "notice_not_future_dated"].failing_rows == 1
    assert {r.total_rows for (d, _), r in got.items() if d == "notices"} == {3}
    assert got["paper_status", "retraction_not_before_publication"].failing_rows == 1
    assert got["paper_status", "status_known"].failing_rows == 0
    assert all(r.passing_rows + r.failing_rows == r.total_rows for r in got.values())

    empty = pipeline.expectation_results({"notices": notice_rows(spark, [])}, pipeline.EXPECTATIONS)
    assert {(r.total_rows, r.failing_rows) for r in empty.collect()} == {(0, 0)}


# A synthetic landing zone: two Retraction Watch snapshots, one edit, one
# delete, one insert, one blank line; plus OpenAlex, PubMed, and the PMC id map.


def rw_record(record_id: int, doi: str, nature: str = "Retraction", reason: str = "+Plagiarism;"):
    from afterlife.sources.retraction_watch import EXPECTED_COLUMNS

    values = {
        "Record ID": str(record_id),
        "Title": f"Paper {record_id}",
        "Subject": "(BLS) Biology - Cellular;",
        "Institution": "Institute of Examples",
        "Journal": "Journal of Examples",
        "Publisher": "Example Press",
        "Country": "Nowhere;",
        "Author": "A Author;B Author",
        "URLS": "",
        "ArticleType": "Research Article;",
        "RetractionDate": "3/14/2021 0:00",
        "RetractionDOI": f"10.9999/notice.{record_id}",
        "RetractionPubMedID": "0",
        "OriginalPaperDate": "1/2/2019 0:00",
        "OriginalPaperDOI": doi,
        "OriginalPaperPubMedID": "0",
        "RetractionNature": nature,
        "Reason": reason,
        "Paywalled": "No",
        "Notes": "",
    }
    return [values[c] for c in EXPECTED_COLUMNS]


def rw_csv(records: list[list[str]]) -> bytes:
    from afterlife.sources.retraction_watch import EXPECTED_COLUMNS

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([*EXPECTED_COLUMNS, ""])  # the upstream file has a trailing comma
    for r in records:
        w.writerow([*r, ""])
    return buf.getvalue().encode()


def build_landing(root: Path, rw_records_claimed: int | None = None) -> None:
    from afterlife.core.landing import LocalStore, land

    store = LocalStore(root)
    blank = [""] * 20
    snapshots = {
        "2026-01-01": [
            rw_record(1, "10.1000/A"),
            rw_record(2, "10.1000/b", nature="Expression of concern"),
            rw_record(3, "Unavailable", nature="Correction"),
        ],
        "2026-01-02": [
            rw_record(1, "10.1000/A"),
            rw_record(2, "10.1000/b", "Expression of concern", "+Plagiarism;+Duplication;"),
            blank,
            rw_record(4, "https://doi.org/10.1000/d"),
        ],
    }
    for d, records in snapshots.items():
        land(
            store,
            source="retraction_watch",
            snapshot_date=d,
            name="retraction_watch.csv",
            raw=rw_csv(records),
            origin_url="synthetic",
            origin_version=d,
            records=rw_records_claimed if rw_records_claimed is not None else len(records),
            notes={"encoding": "utf-8"},
        )
    works = [
        {
            "id": "https://openalex.org/W1",
            "doi": "https://doi.org/10.1000/A",
            "ids": {"doi": "https://doi.org/10.1000/A", "pmid": None, "pmcid": None},
            "display_name": "Paper 1",
            "publication_year": 2019,
            "publication_date": "2019-01-02",
            "type": "article",
            "cited_by_count": 5,
            "counts_by_year": [{"year": 2024, "cited_by_count": 2}],
            "is_retracted": True,
            "field": "Biology",
            "domain": "Life Sciences",
        },
        {
            "id": "https://openalex.org/W2",
            "doi": None,
            "ids": {"doi": None, "pmid": "https://pubmed.ncbi.nlm.nih.gov/67890", "pmcid": None},
            "display_name": "Paper Z",
            "publication_year": 2020,
            "publication_date": "2020-05-01",
            "type": "article",
            "cited_by_count": 0,
            "counts_by_year": [],
            "is_retracted": True,
            "field": None,
            "domain": None,
        },
    ]
    land(
        store,
        source="openalex",
        snapshot_date="2026-01-02",
        name="works_retracted.jsonl",
        raw="".join(json.dumps(w) + "\n" for w in works).encode(),
        origin_url="synthetic",
        origin_version="api",
        records=len(works),
    )
    pmids = {"term": '"retracted publication"[pt]', "pmids": ["12345", "67890"]}
    land(
        store,
        source="pubmed",
        snapshot_date="2026-01-02",
        name="retracted_publication_pmids.json",
        raw=json.dumps(pmids).encode(),
        origin_url="synthetic",
        origin_version="esearch",
        records=2,
        notes={"complete": True},
    )
    land(
        store,
        source="pmc_ids",
        snapshot_date="2026-01-02",
        name="PMC-ids.csv",
        raw=b"DOI,PMCID,PMID,Release Date\n"
        b"10.1000/a,PMC111,,2020-01-01\n"
        b",PMC222,67890,2021-01-01\n",
        origin_url="synthetic",
        origin_version="test",
    )


def job_argv(task: str, tmp: Path, *extra: str) -> list[str]:
    return [
        f"--task={task}",
        f"--lake-root={tmp / 'lake'}",
        f"--landing-root={tmp / 'landing'}",
        f"--runs-root={tmp / 'runs'}",
        "--run-id=7",
        *extra,
    ]


@pytest.mark.spark
def test_entry_point_end_to_end_on_a_synthetic_lake(spark, jobs, tmp_path):
    from afterlife.spark.session import Lake

    build_landing(tmp_path / "landing")
    assert jobs.main(job_argv("bronze", tmp_path)) == 0
    assert jobs.main(job_argv("silver", tmp_path)) == 0
    bronze, silver = reports(tmp_path / "runs")
    assert bronze["command"] == "bronze" and bronze["status"] == "succeeded"
    assert [r["rows"] for r in bronze["report"]["retraction_watch"]] == [3, 4]
    assert bronze["report"]["freshness"]["retraction_watch"]["newest"] == "2026-01-02"
    s = silver["report"]
    assert s["replay"][1]["blank_rows_dropped"] == 1
    assert s["rw"] == {"notices": 3, "papers": 3}
    assert s["openalex"]["works"] == 2 and s["pubmed"]["pmids"] == 2
    assert s["pmc_ids"]["pmcids"] == 2 and "skipped" in s["pmc"]
    assert s["quarantined"] == []

    lake = Lake(root=str(tmp_path / "lake"))
    changes = {
        (r.record_id, r.change_type): list(r.changed_columns)
        for r in lake.read(spark, "silver", "rw_changes").collect()
    }
    assert changes == {("2", "update"): ["reason"], ("3", "delete"): []}

    # Retries are safe: a second bronze run ingests nothing.
    assert jobs.main(job_argv("bronze", tmp_path)) == 0
    again = reports(tmp_path / "runs")[-1]["report"]
    assert again["retraction_watch"] == [] and again["pubmed"] == []

    # The publication layer over the silver this run produced, wired as the
    # pipeline wires it, with `dlt` replaced by the recording stub.
    stub = RecordingDlt()
    mod = load_pipeline(stub)
    mod._silver = lambda table: lake.read(spark, "silver", table)
    notices = stub.tables["notices"]["fn"]()
    assert tuple(notices.columns) == mod.NOTICE_COLUMNS
    published = mod.enforce(notices, mod.EXPECTATIONS["notices"])
    assert sorted(r.doi for r in published.collect()) == ["10.1000/a", "10.1000/b", "10.1000/d"]
    results = stub.tables["expectation_results"]["fn"]().collect()
    assert {(r.dataset, r.failing_rows) for r in results} == {("notices", 0), ("paper_status", 0)}


@pytest.mark.spark
def test_bronze_row_count_mismatch_exits_2(spark, jobs, tmp_path):
    build_landing(tmp_path / "landing", rw_records_claimed=99)
    with pytest.raises(SystemExit) as exc:
        jobs.main(job_argv("bronze", tmp_path))
    assert exc.value.code == jobs.EXIT_CONTRACT
    (report,) = reports(tmp_path / "runs")
    assert report["status"] == "contract_violation"
    assert "Spark parsed 3 rows, landing counted 99" in report["error"]


@pytest.mark.spark
def test_silver_refuses_to_run_without_bronze(spark, jobs, tmp_path):
    with pytest.raises(SystemExit) as exc:
        jobs.main(job_argv("silver", tmp_path))
    assert exc.value.code == jobs.EXIT_CONTRACT
    assert "bronze tables missing" in reports(tmp_path / "runs")[0]["error"]


@pytest.mark.spark
@pytest.mark.data
def test_doi_constraint_agrees_with_python_on_landed_dois(spark, pipeline):
    """House rule 7: the pipeline's DOI check reimplements core.ids.DOI_SHAPE,
    so it is compared with the Python rule over every DOI string in the lake."""
    from pyspark.sql import functions as F

    from afterlife.core import ids
    from afterlife.spark.session import Lake

    lake = Lake(root=str(ROOT / "data" / "lake"))
    if not lake.exists(spark, "silver", "rw_history"):
        pytest.skip("no local lake under data/lake")
    h = lake.read(spark, "silver", "rw_history")
    raw = h.select(F.col("original_paper_doi").alias("v")).union(h.select("retraction_doi"))
    if lake.exists(spark, "silver", "openalex_works"):
        raw = raw.union(lake.read(spark, "silver", "openalex_works").select("doi"))
    doi = raw.select(F.lower(F.trim("v")).alias("doi")).distinct()
    got = doi.select("doi", F.expr(pipeline.DOI_SHAPED.constraint).alias("ok")).collect()
    assert len(got) > 1000
    mismatched = [r.doi for r in got if r.ok != (r.doi is None or bool(ids.DOI_SHAPE.match(r.doi)))]
    assert not mismatched, mismatched[:10]


# parity: the Databricks export against afterlife/export.py -------------------

REGISTRY = [
    ("paper_key", "VARCHAR", "string"),
    ("doi", "VARCHAR", "string"),
    ("pmid", "VARCHAR", "string"),
    ("pmcid", "VARCHAR", "string"),
    ("openalex_id", "VARCHAR", "string"),
    ("title", "VARCHAR", "string"),
    ("field", "VARCHAR", "string"),
    ("cited_by_count", "BIGINT", "bigint"),
    ("rw_status", "VARCHAR", "string"),
    ("rw_keys", "BIGINT", "bigint"),
    ("first_retraction_date", "DATE", "date"),
    ("last_retraction_date", "DATE", "date"),
    ("original_date", "DATE", "date"),
    ("n_notices", "BIGINT", "bigint"),
    ("natures", "VARCHAR", "string"),
    ("flag_openalex", "BOOLEAN", "boolean"),
    ("flag_pubmed", "BOOLEAN", "boolean"),
    ("flag_pmc", "BOOLEAN", "boolean"),
    ("n_flagging", "INTEGER", "int"),
    ("verdict", "VARCHAR", "string"),
]
d1, d2 = date(2019, 1, 2), date(2021, 3, 14)
REGISTRY_ROWS = [
    ("doi:10.1000/a", "10.1000/a", "111", "PMC1", "W1", "Paper A", "Biology", 12, "retracted",
     1, d2, d2, d1, 2, "Expression of concern;Retraction", True, True, True, 4, "retracted"),
    ("doi:10.1000/b", "10.1000/b", None, "PMC2", "W2", "Paper B", None, 3, "concern",
     1, None, None, d1, 1, "Expression of concern", True, False, None, 1, "disputed"),
    ("doi:10.1000/c", "10.1000/c", None, "PMC3", None, "Paper C", None, None, "corrected",
     1, None, None, d1, 1, "Correction", False, False, False, 0, "corrected"),
    ("pmid:999", None, "999", None, None, None, None, None, None,
     None, None, None, None, None, None, False, True, None, 1, "retracted"),
]  # fmt: skip
IDENTITY_ROWS = [
    ("doi:10.1000/a", "retraction_watch", "doi:10.1000/a"),
    ("doi:10.1000/a", "openalex", "W1"),
    ("doi:10.1000/b", "retraction_watch", "doi:10.1000/b"),
    ("doi:10.1000/c", "retraction_watch", "rw:3"),
    ("pmid:999", "pubmed", "999"),
]
NOTICE_ROWS = [
    ("doi:10.1000/a", 2, "Retraction", ["Plagiarism", "Duplication"]),
    ("doi:10.1000/a", 1, "Expression of concern", ["Plagiarism"]),
    ("doi:10.1000/b", 5, "Expression of concern", []),
    ("rw:3", 3, "Correction", None),
]


@pytest.mark.spark
def test_databricks_export_matches_the_laptop_export(spark, jobs, tmp_path, monkeypatch):
    duckdb = pytest.importorskip("duckdb")
    pq = pytest.importorskip("pyarrow.parquet")
    from afterlife_jobs import export as uc

    from afterlife import export as laptop

    landing = tmp_path / "landing"
    build_landing(landing)
    monkeypatch.setenv("AFTERLIFE_LANDING", str(landing))

    # The laptop path, with DuckDB tables standing in for gold and a table
    # macro standing in for delta_scan over silver.rw_notices.
    con = duckdb.connect()
    con.execute("create schema gold")
    con.execute(f"create table gold.registry ({', '.join(f'{n} {t}' for n, t, _ in REGISTRY)})")
    marks = ", ".join("?" * len(REGISTRY))
    con.executemany(f"insert into gold.registry values ({marks})", REGISTRY_ROWS)
    con.execute(
        "create table gold.int_paper_identity (paper_key text, source text, source_key text)"
    )
    con.executemany("insert into gold.int_paper_identity values (?, ?, ?)", IDENTITY_ROWS)
    con.execute(
        "create table notices_src (paper_key text, record_id bigint, nature text, reasons text[])"
    )
    con.executemany("insert into notices_src values (?, ?, ?, ?)", NOTICE_ROWS)
    con.execute("create macro delta_scan(p) as table select * from notices_src")
    monkeypatch.setattr(laptop, "_connect", lambda: con)
    laptop_report = laptop.run(tmp_path / "laptop")

    # The Databricks path, with temp views standing in for Unity Catalog.
    spark_types = ", ".join(f"{n} {t}" for n, _, t in REGISTRY)
    spark.createDataFrame(REGISTRY_ROWS, spark_types).createOrReplaceTempView("t_registry")
    spark.createDataFrame(
        IDENTITY_ROWS, "paper_key string, source string, source_key string"
    ).createOrReplaceTempView("t_identity")
    spark.createDataFrame(
        NOTICE_ROWS, "paper_key string, record_id bigint, nature string, reasons array<string>"
    ).createOrReplaceTempView("t_notices")
    uc_report = uc.run(
        spark,
        tmp_path / "uc",
        str(landing),
        registry="t_registry",
        identity="t_identity",
        notices="t_notices",
    )

    assert uc_report["documents"] == laptop_report["documents"] == len(REGISTRY_ROWS)
    assert uc_report["evidenceops_records"] == laptop_report["evidenceops_records"] == 3
    as_of = laptop_report["as_of"]
    reg = Path("registry") / f"as_of={as_of}"
    for rel in (reg / "_coverage.json", Path("evidenceops") / "trust_registry.jsonl"):
        assert (tmp_path / "uc" / rel).read_bytes() == (tmp_path / "laptop" / rel).read_bytes(), rel
    gz = reg / "registry.jsonl.gz"
    ours = gzip.decompress((tmp_path / "uc" / gz).read_bytes())
    theirs = gzip.decompress((tmp_path / "laptop" / gz).read_bytes())
    assert ours == theirs
    doc = json.loads(ours.splitlines()[0])
    assert doc["retraction_watch"]["record_ids"] == [1, 2]
    assert doc["retraction_watch"]["reasons"] == ["Duplication", "Plagiarism"]

    def rows(path):
        t = pq.read_table(path)
        return t.column_names, sorted(t.to_pylist(), key=lambda r: r["paper_key"])

    assert rows(tmp_path / "uc" / reg / "parquet" / "registry.parquet") == rows(
        tmp_path / "laptop" / reg / "parquet" / "registry.parquet"
    )
    assert jobs.TASKS["export"] is jobs.run_export
