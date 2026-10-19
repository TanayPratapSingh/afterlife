# afterlife on Databricks

The transformation half of afterlife, packaged as one Databricks Asset Bundle
(the v1.21.0 CLI schema calls them Declarative Automation Bundles). It deploys
unchanged to **Databricks Free Edition** (target `free`, the default), and to a
paid workspace on AWS (target `aws`). Nothing here has run in a workspace yet;
the table at the end says exactly what ran where.

## What is here

| File | What it is | Why it is built this way |
|---|---|---|
| `databricks.yml` | The bundle: variables, the wheel artifact, targets `free` and `aws` | One definition of the job and pipeline; targets differ only in compute, storage, and identity |
| `resources/afterlife_daily.job.yml` | The Lakeflow Job: setup, fetch, bronze, silver, pipeline, dbt gold, export | Compute is bound per target at the bottom of this file, because a task cannot carry both `environment_key` and `job_cluster_key`, and an override can add a key but never remove one |
| `resources/registry_quality.pipeline.yml` | The Lakeflow Declarative Pipeline (formerly Delta Live Tables) | Serverless on both targets, triggered by the job, event log published to `ops` |
| `pipelines/registry_quality.py` | Pipeline source: contract checked `publication.notices`, `publication.paper_status`, `publication.expectation_results` | Transforms are plain functions over DataFrames so pytest runs them; the `dlt` decorators apply only inside a pipeline |
| `src/afterlife_jobs/main.py` | The `afterlife_job` entry point every Python task calls, with `--task` | Parameters in, a run report out to `/Volumes/<catalog>/ops/runs`, exit 2 on a contract violation |
| `src/afterlife_jobs/export.py` | The export task, reading gold from Unity Catalog | Supplies only a Spark query and a parquet writer; file layout, write order, and the documents come from `afterlife.export.write()` and `document()`, and a parity test requires identical files to the laptop export |
| `sql/setup.sql` | Catalog, schemas, volumes, comments, example grants, table property guidance | A SQL file task on the warehouse, idempotent, run on every job run |
| `sql/aws_landing.sql` | One time admin script for the `aws` target: external location and volume over the S3 bucket | Run by a metastore admin, never by a job |
| `notebooks/explore_registry.sql` | A reviewer's walk through gold, the publication contract, SCD2 history, Delta history and change data feed, run reports | Databricks source format, read only |

No AI/BI dashboard (`.lvdash.json`) is included: its serialized format was
not verified here, and a dashboard that fails to import is worse than none.

## The daily job

```
setup -> fetch_gate -(true)-> fetch_retraction_watch -+
  |                  \-(true)-> fetch_pubmed ----------+
  +-------------------------------------------------- bronze -> silver -+-> registry_quality -+
                                                                        +-> gold (dbt) -------+-> export
```

* **setup** runs `sql/setup.sql` on the SQL warehouse with the task parameter
  `catalog`. The file uses the marker `:catalog` inside `IDENTIFIER()` because a
  bundle substitutes `${var.*}` only in YAML, never inside the files it deploys.
* **fetch_gate** is a `condition_task` on the job parameter `fetch_enabled`.
  Both fetch tasks hang off its `true` outcome.
* **bronze** depends on setup and on both fetches with `run_if: NONE_FAILED`.
  With fetching off, the fetches are excluded, setup ran, nothing failed, so
  bronze runs on whatever is already in the landing volume. When a fetch
  fails, bronze does not run. Before starting Spark it refuses an empty landing
  zone (exit 2, with the upload instructions in the error).
* **silver** replays new Retraction Watch snapshots through the SCD2 MERGE and
  rebuilds the rest of silver, then checks cheap post conditions (no empty
  table, never more papers than notices).
* **registry_quality** and **gold** both read silver, so they run side by side.
  **export** waits for both.

Every task has a timeout; every compute task retries once (the fetches twice,
five minutes apart). Retries are safe: bronze skips objects it has already
ingested (tested), and silver skips snapshots already replayed. The schedule is
23:45 UTC daily, after the 23:00 UTC upstream publish, with
`max_concurrent_runs: 1` and queueing so a slow run delays the next instead of
racing it. Failures email `notification_email`, which defaults to the deploying
user.

The silver timeout is two hours. The only measurement behind it is the local
full rebuild: 600.1 s on 4 cores (`runs/20261019T194028Z-silver.json`).
Serverless timing has not been measured.

## The publication contract

| Expectation | Datasets | Action | Why that severity |
|---|---|---|---|
| `paper_key_present` | notices, paper_status | **fail** the update | A null key means identity resolution broke; publishing would silently drop papers from every consumer's join |
| `doi_shape` | notices, paper_status | **drop** the row | silver already nulls malformed DOIs, so this should drop nothing; it catches a regression and the event log counts it |
| `notice_not_future_dated` | notices | warn | An upstream data error worth seeing, not a reason to withhold the notice |
| `status_known` | paper_status | warn | The status vocabulary of `core/status.py` |
| `retraction_not_before_publication` | paper_status | warn | Retraction dated before the paper: an upstream date error |

The DOI regex is the one in `afterlife/spark/expressions.py`, escaped for a
Spark SQL literal. A test requires the copies to be equal, and another compares
the SQL constraint with `core.ids.DOI_SHAPE` over every DOI string in the local
lake (house rule 7). Every constraint is NULL free, so how the runtime treats a
NULL result never decides an outcome.

The pipeline uses `import dlt`. Current runtimes also expose the API as
`pyspark.pipelines`; `dlt` was kept because the newer module's decorator
surface was not verified here. `expectation_results` repeats the event log's
counts as a plain table so they are queryable with one SELECT and testable
without a workspace.

## Free Edition limits that shaped the design

From the Free Edition limitations page (docs.databricks.com, read 2026-10-19):

* **Serverless compute only, no custom compute configurations.** Every task
  runs in a serverless environment (`environment_version: "5"`, Python 3.12,
  the version the v1.21.0 CLI templates generate). The wheel is pure Python
  with no dependencies, so it never fights the environment's own pyspark.
* **Outbound internet limited to a set of trusted domains.** Whether
  gitlab.com and eutils.ncbi.nlm.nih.gov are on it is not stated, so
  `fetch_enabled` defaults to `"false"` and the landing zone is uploaded from
  the laptop. Try `--params fetch_enabled=true` once; if the fetch tasks fail on
  the network, egress is blocked and nothing else changes.
* **One SQL warehouse, `2X-Small`.** setup and dbt share it, found by name with
  a variable lookup (`Serverless Starter Warehouse`, not verified; override with
  `--var warehouse_id=<id>`).
* **At most 5 concurrent job tasks per account.** The DAG is never wider than
  2 tasks (a test computes the largest antichain).
* **One active pipeline per pipeline type.** There is one pipeline.
* **Quotas: exceed one and compute stops for the rest of the day.** Nothing
  here runs continuously; the pipeline is triggered, not continuous.
* **No account console or account level APIs, no SSO or SCIM.** There are no
  account groups to grant to, so the `free` target runs as you and the grants
  in `setup.sql` stay commented out.
* **No custom storage locations.** Everything is Unity Catalog managed: the
  landing zone is the managed volume `landing.files`, not an external location.
* **Serverless rejects `cache()`.** `afterlife/spark/session.py` already falls
  back through `persist()`, so bronze and silver run unchanged.
* **Serverless environments cache a wheel by version.** The `free` target sets
  `presets.artifacts_dynamic_version`, so a redeploy of 0.1.0 is not silently
  ignored.
* **Development mode deploys the schedule paused.** To run daily on Free
  Edition, add `trigger_pause_status: UNPAUSED` under `presets` in the `free`
  target, keeping the daily quota in mind.

## The aws target

| | free | aws |
|---|---|---|
| Mode | development (names prefixed `[dev you]`, schedule paused) | production (schedule active, explicit `root_path`) |
| Compute | serverless environments | job cluster, DBR `15.4.x-scala2.12`, `m6gd.xlarge`, 1 to 4 workers, spot with fallback, dedicated access mode |
| Libraries | environment dependencies | task `libraries` (the wheel; `dbt-databricks` from PyPI) |
| Landing | managed volume `/Volumes/afterlife/landing/files` | external volume `/Volumes/afterlife/landing/s3` over `s3://afterlife-landing-<account>/` |
| Exports | `/Volumes/afterlife/ops/exports` | `.../landing/s3/export`, the prefix whose `registry/*.jsonl.gz` triggers `aws/lambdas/load_registry` |
| Identity | the deploying user | `run_as` a service principal (`--var service_principal=<application id>`) |
| Permissions | none | `afterlife-engineers` CAN_MANAGE, `afterlife-readers` CAN_VIEW |
| Ingest | upload, or `fetch_enabled=true` | the fetch Lambdas in `aws/` write the bucket; `fetch_enabled` stays `"false"` |

Before the first `-t aws` deploy, a metastore admin creates the storage
credential for the bucket's IAM role and runs `sql/aws_landing.sql`. The pipeline
stays serverless on AWS: classic pipeline compute would need edition `ADVANCED`
for expectations, and serverless pipelines exist on paid AWS workspaces. The
export writes `_coverage.json` before `registry.jsonl.gz`, because the Lambda
fires on the `.jsonl.gz` and reads its sibling coverage at that moment.

## Deploy to Free Edition, step by step

Everything below is for you to run. No account was created, no credentials
were used, and no Databricks API was called while building this bundle.

1. **Sign up** for Free Edition yourself:
   https://login.databricks.com/?dbx_source=docs&intent=CE_SIGN_UP
2. **Install the CLI**, v0.268.0 or later. Either `brew tap databricks/tap &&
   brew install databricks`, or the release binary with its checksum, which is
   how `.tools/databricks` was installed:
   ```bash
   V=1.21.0; A=darwin_arm64
   curl -fLO https://github.com/databricks/cli/releases/download/v$V/databricks_cli_${V}_${A}.tar.gz
   curl -fLO https://github.com/databricks/cli/releases/download/v$V/databricks_cli_${V}_SHA256SUMS
   grep "_${A}.tar.gz" databricks_cli_${V}_SHA256SUMS | shasum -a 256 -c -
   mkdir -p .tools && tar -xzf databricks_cli_${V}_${A}.tar.gz -C .tools databricks
   ```
3. **Package the entry point.** Apply the `pyproject.toml` change below, then
   `pip install build` (the artifact runs `python -m build --wheel`).
4. **Log in**, which opens a browser and saves a profile:
   `databricks auth login --host https://<your-workspace>.cloud.databricks.com`
5. **Validate and deploy** from this directory:
   ```bash
   cd databricks
   databricks bundle validate -t free
   databricks bundle deploy -t free
   ```
6. **Create the catalog and volumes** by running only the setup task:
   `databricks bundle run afterlife_daily -t free --only setup`.
   If `CREATE CATALOG` is refused, create `afterlife` in Catalog Explorer, or
   deploy into the catalog your workspace already has with
   `--var catalog=<name> --var landing_root=/Volumes/<name>/landing/files
   --var exports_root=/Volumes/<name>/ops/exports`.
7. **Load the landing zone** (see below).
8. **Run the job:** `databricks bundle run afterlife_daily -t free`. Run
   reports land in `/Volumes/afterlife/ops/runs/`; open
   `notebooks/explore_registry` from the bundle's workspace folder to look around.
9. **After editing the pipeline**, check its graph without writing anything
   (silver must exist by then):
   `databricks bundle run registry_quality -t free --validate-only`
10. **Optional, test egress:**
    `databricks bundle run afterlife_daily -t free --params fetch_enabled=true`

### Load the landing zone

The local landing zone is 766 MB (`du -sh data/landing` on 2026-10-19:
retraction_watch 510 MB, pmc_ids 245 MB, openalex 11 MB, pubmed 120 KB).
`fs cp -r` copies the contents of the source into the target, keeping the
`landing/<source>/snapshot_date=.../` layout that bronze reads:

```bash
databricks fs cp -r data/landing dbfs:/Volumes/afterlife/landing/files/landing
databricks fs ls dbfs:/Volumes/afterlife/landing/files/landing   # retraction_watch, openalex, pubmed, pmc_ids
```

Do not pass `--overwrite`: the landing zone is append only (ADR 0004). To add
snapshots later, copy only the new `snapshot_date=` directories.

**gold needs `pmc_metadata`.** `silver.build_pmc` skips it when absent, but
`warehouse/models/intermediate/int_paper_identity.sql` reads
`silver.pmc_metadata`, and the local landing zone has no `pmc_metadata` yet. Until it is
landed (`afterlife fetch pmc` on the laptop, then upload), the gold task will fail.

## The `pyproject.toml` change this needs

`afterlife_jobs` lives in `databricks/src`, outside the package search path:

```toml
[project.scripts]
afterlife = "afterlife.cli:main"
afterlife_job = "afterlife_jobs.main:main"

[tool.setuptools.packages.find]
where = [".", "databricks/src"]
include = ["afterlife*"]
```

Built in a scratch copy of the repository with this change, the wheel
contains `afterlife` and `afterlife_jobs` and declares the `afterlife_job`
console script. Installed into a clean venv without pyspark, the entry point
resolves through the distribution metadata and exits 2 on an empty landing
zone. `tests/test_databricks.py::test_pyproject_packages_the_entry_point`
skips until the change is made, then enforces it.

## Deployed to Free Edition, 2026-10-19

The bundle was deployed to a Free Edition workspace and the job run twice.

- **Catalog.** `databricks catalogs create afterlife` was refused: the account
  uses Default Storage, which only the UI can attach to a new catalog. The
  bundle was deployed into the existing `workspace` catalog instead:
  `--var catalog=workspace --var landing_root=/Volumes/workspace/landing/files
  --var exports_root=/Volumes/workspace/ops/exports --var warehouse_id=<id>`.
- **Validation.** `bundle validate -t free` passed against the live workspace.
- **Setup.** The SQL file task ran in about 40 s and created the schemas and
  volumes, so `IDENTIFIER(:catalog)` in a SQL file task works.
- **Landing.** `databricks fs cp -r data/landing ...` uploaded 769 MB, 86 files,
  in 172 s.
- **First full run** (fetch tasks off): every task succeeded in about 36
  minutes. Silver replayed all 39 snapshots and built the same history as the
  laptop: 152,336 versions of 73,334 records, 73,283 current, and the same
  2026-10-15 snapshot quarantined with 5,520 lossy records. Gold row counts
  matched the laptop for every mart.
- **One difference found:** 2 of 92,097 exported documents carried a different
  PMID than the laptop export, because `F.first` in silver is order dependent.
  It is now a deterministic `max_by`.
- **Second run** (incremental: bronze and the replay skipped): succeeded in
  510 s. The uncompressed export is byte identical to the laptop's; the gzip
  streams differ only because the two Pythons link different zlib builds.
- **Not exercised:** the fetch tasks (off on Free Edition), the `aws` target,
  and the AWS Step Functions HTTP Task that would start this job.

## Executed locally, validated statically, not executed

Local means this laptop on 2026-10-19: pyspark 3.5.7, delta-spark 3.3.2,
Python 3.12.7, Databricks CLI v1.21.0.

| Executed locally | Validated statically | Not executed |
|---|---|---|
| Entry point dispatch: both fetch tasks with the sources mocked, export's refusal without a catalog | `databricks.yml`, the job, and the pipeline YAML against the CLI's own JSON schema (0 errors; a copy with injected typos is rejected) | Any deploy, job run, pipeline update, or dbt run in a workspace |
| bronze and silver end to end on a synthetic lake through the entry point, rerun safety, exit 2 on a row count mismatch and on missing inputs | Structure tests: paths exist, `depends_on` names real tasks, no cycles, width 2 or less, compute bound once per target, wheel task argv parses | The `dlt` runtime (a recording stub stood in), `cluster_by` and `table_properties` in `@dlt.table` |
| Pipeline transforms and expectation semantics on local Spark, over the silver that run produced | `setup.sql` and `aws_landing.sql`: statement shapes, the `:catalog` marker, every schema and volume the bundle names | `IDENTIFIER(:catalog)` in a SQL file task; `CREATE CATALOG` rights on Free Edition |
| The SQL DOI constraint against `core.ids.DOI_SHAPE` over every DOI string in `data/lake` | The notebook's source format and the schemas it reads | Serverless environment 5, the wheel on it, DeltaTable MERGE over Spark Connect, volume paths |
| The Databricks export against `afterlife/export.py` on synthetic gold: identical jsonl, coverage, trust lines, parquet rows | | The warehouse lookup name; egress from Free Edition; the whole `aws` target |
| Wheel build with the proposed `pyproject.toml` change, in a clean venv | | |
| `databricks bundle validate -t free` and `-t aws` with no credentials: the bundle loaded with no warnings and stopped at auth (`default auth: cannot configure default credentials`) | | |
