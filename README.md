# afterlife

**What happens to a scientific paper after it is retracted, and who notices.**

A lakehouse that builds one retraction registry out of four public ones
(Retraction Watch, OpenAlex, PubMed, PMC), measures how badly they disagree,
historizes two years of daily Retraction Watch snapshots, and serves a lookup
API that never answers "ok", only "no registry here flags this, as of this
date". Ingest and serving are built for AWS, transformation for Databricks,
and the gold layer is dbt that runs on four warehouses.

It is the upstream half of EvidenceOps, a separate retrieval system (not yet
published) that refuses to cite retracted papers. EvidenceOps needs a registry to check
against. This project builds one, and found that no single public source is
good enough to be it.

Everything below was run on real public data on 2026-10-19, on a laptop, and
the same code was then deployed to a live AWS account and a Databricks Free
Edition workspace (see [Live deployment](#live-deployment)). Every number
names the run report it came from. The canonical one is
`runs/20261019T224600Z-findings.json`; [RESULTS.md](RESULTS.md) has every
table with its key.

---

## What it found

**No registry is complete.** 89,681 papers are flagged as retracted by at
least one registry. The registries that could have flagged a paper all agree
on only 46,225 of them (51.5%), and 36,401 (40.6%) are flagged by exactly one.

| Registry | Eligible papers | Eligible | Flags | Recall against the union |
| --- | --- | ---: | ---: | ---: |
| PMC | with a linked PMC open copy | 19,499 | 18,748 | 96.1% |
| PubMed | with a PMID | 37,495 | 33,302 | 88.8% |
| OpenAlex | with a DOI or PMID | 84,162 | 68,945 | 81.9% |
| Retraction Watch | every paper | 89,681 | 67,672 | 75.5% |

The union is not ground truth. It is the most generous reading of "somebody
says this is retracted" ([ADR 0001](docs/adr/0001-no-registry-is-ground-truth.md)).

**Half of OpenAlex's "retracted works" are retraction notices.** OpenAlex
reports 136,970 works with `is_retracted: true`. Of the 136,873 fetched,
67,544 (49.3%) are the notices rather than the papers they retract: 38,161
have type `retraction`, and 29,383 more carry a DOI or PMID that Retraction
Watch records as a notice. Counted as papers, they would nearly double the
registry. PMC gets this right: of the 17,951 notice articles it holds, 187
carry `is_retracted`.

**Retracted papers keep getting cited.** Of 44,557 retracted papers whose
whole post retraction period falls inside OpenAlex's citation window
(excluding 486 "retract and replace" papers, reported separately), 29,024
(65.1%) were cited at least once in a year after their retraction year: 217,376
citations in total. The median paper got 1 and the 90th percentile 11.

**Retraction is slow.** Across 67,845 dated retraction notices, the median time
from publication to retraction is 493 days (interquartile range 156 to 1,074).
For notices issued so far in 2026 it is 1,001 days.

**The 2023 wave was paper mills and broken peer review.** Notices citing
"Paper Mill" went from 4 in 2019 to 6,782 in 2023; "Computer-Aided Content or
Computer-Generated Content" from 1 to 5,588; "Concerns/Issues about Peer
Review" from 0 to 7,870.

**The source rewrites its own history.** Of 73,334 Retraction Watch records
seen across 39 snapshots, 50,518 (68.9%) were edited after they first
appeared. Most of that is the reason taxonomy being relabelled ("Fake Peer
Review" became "Compromised Peer Review" on 10,426 records on 2026-01-02).
3,840 records (5.2%) changed in something other than reasons, including 96
whose DOI changed and 54 whose retraction date changed. A pipeline that only
appends new rows sees none of it.

**Ask the same question twice, get two answers.** Four questions about
retractions dated 2023 or earlier, fixed before any was computed, asked of
every snapshot (key `as_of` in `runs/20261027T004647Z-findings.json`). From
2024-09-02 to 2026-10-16 the count grew 9.1% (48,789 to 53,227), the count
citing "Paper Mill" grew 80.6% (4,868 to 8,790), the median lag rose from 379
to 420 days, and "Fake Peer Review" fell from 4,654 to zero on a relabel.
Nearly all of it is late cataloguing: 4,482 records dated 2023 or earlier were
added after the first snapshot, 3,921 of them Hindawi. A study of 2023 run in
September 2024 would have seen 9,030 retractions; the database now holds
13,337. A draft paper on this is in [paper/](paper/).

**Most single registry flags are real.** A blind hand audit of 200 of the
36,401 papers only one registry flags, labelled from publishers' pages against
a codebook committed before any label ([docs/audit-codebook.md](docs/audit-codebook.md)),
estimates that 95.0% (91.5% to 98.6%) were in fact retracted
(`runs/20261027T221642Z-audit-score.json`). The registries disagree mostly by
omission, not by error; notices counted as papers were 6 of the 200.

## Incidents the pipeline caught

Each was found on real data, broke something, and is now handled in code with
a test. They are why the design looks the way it does.

1. **A daily snapshot in the wrong encoding, and lossy.** The Retraction Watch
   file for 2026-10-15 was Windows-1252 instead of UTF-8 (35,954 invalid UTF-8
   bytes), with 12 hour timestamps, zero padded PMIDs, and line breaks inside
   cells. Every character outside Windows-1252 had become `?`: `Wnt/β-catenin`
   became `Wnt/?-catenin` in 2,298 titles and 2,922 author lists. A byte level
   history recorded 73,214 edits that day and 73,253 the next
   (`runs/20261019T192629Z-silver.json`). Landing now records each file's
   encoding, bronze reads each file with its own, and a quality gate
   quarantines the snapshot with its evidence instead of merging it
   ([ADR 0004](docs/adr/0004-history-on-meaning-quarantine-on-loss.md)).
2. **Format churn posing as edits.** On 2025-09-02 the export dropped a `+`
   prefix from every reason label: 65,898 records changed bytes and not
   meaning. History is now cut on a semantic hash (type 2) and raw strings are
   refreshed in place (type 1). Capitalization changes in controlled
   vocabularies count as format too.
3. **Blank rows.** Six snapshots from 2026-04-01 to 2026-09-01 contain lines of
   nothing but commas, 1,054 in total, which collided as duplicate NULL keys in
   the MERGE. Bronze keeps them; silver drops and counts them.
4. **An API that changed its access model mid build.** OpenAlex now prices every
   call and requires a key for cursor paging. The fetcher meters spend against a
   budget and, without a key, partitions by publication date to stay under the
   10,000 result ceiling, checking that the slices add up: 709 calls, $0.0709,
   97 works unreachable by date and reported rather than dropped.
5. **Notices counted as papers.** The OpenAlex finding above. Exclusions are a
   mart of their own (`gold.flag_exclusions`), so the registry reconciles with
   every source's raw count.
6. **PubMed slices that overlap.** Its publication date filter matches both
   print and electronic dates, so year slices sum to 39,028 while the true
   total is 35,169. Completeness is checked on the union of ids.
7. **A Spark and Delta version skew.** With PySpark 3.5.7 and Delta 3.3.2,
   every `REPLACE TABLE AS SELECT` fails with "does not support truncate in
   batch mode", which breaks dbt-spark. Reproduced outside dbt and pinned to
   PySpark 3.5.3, the version Delta 3.3 is built against.
8. **Two regex dialects.** Python's `str.strip()` removes a no break space and
   Spark's `trim` does not, so the Python and Spark DOI rules disagreed on that
   input. The Spark expression now mirrors Python's whitespace set exactly
   ([ADR 0003](docs/adr/0003-python-reference-spark-fast-path.md)).
9. **Spark 4 rejects Windows-1252.** Spark 4.0 accepts only a short list of
   CSV charsets, so reading the cp1252 snapshot failed on the Spark that
   Databricks Runtime 17 and serverless run. Non UTF-8 files are now
   transcoded in Python into a derived `staging/` copy; on the real snapshot
   the new path yields the same 73,253 rows as the old one. Found by CI on its
   first run.
10. **A date literal that only works on macOS.** `F.lit(date.min)` goes
    through `strftime("%Y-%m-%d")`, which glibc renders as `1-01-01` for year
    1, and `java.sql.Date` rejects it. Tests passed on the Mac and failed on
    Linux CI, which is what Databricks runs. The sentinel is now built inside
    Spark. Also found by CI on its first run.

## The stack

| Layer | Technology | What it does here | Status |
| --- | --- | --- | --- |
| Landing | S3 (locally, a directory) | Raw bytes, sha256 manifests, `If-None-Match: *` writes that refuse to overwrite | executed locally; S3 path tested with moto |
| Ingest | Lambda, ECS Fargate, ECR, EventBridge Scheduler, Step Functions, SQS | Daily fetchers. OpenAlex runs on Fargate: its fetch took 9 minutes 42 seconds, two thirds of Lambda's 15 minute ceiling | **deployed**: Lambda and Fargate fetches and the Step Functions run succeeded live |
| Transform | Spark 3.5 and Delta Lake, Databricks Unity Catalog, serverless | Manifest driven bronze; SCD2 replay of 39 snapshots with Delta MERGE and change data feed; the quality gate | **deployed**: the Databricks job built the same SCD2 history as the laptop (152,336 versions) |
| Orchestration | Databricks Asset Bundle, Lakeflow Jobs, Lakeflow Declarative Pipeline | Daily DAG; pipeline expectations as a publication contract | **deployed**: job and pipeline ran green on Free Edition |
| Gold | dbt on DuckDB, Spark SQL, Databricks, Snowflake, BigQuery | Identity across DOI, PMID, PMCID; registry, agreement, lag, afterlife, reasons, mutability | DuckDB and Spark compared row by row; **ran on a Databricks SQL warehouse**; Snowflake and BigQuery `dbt parse` only |
| Serving | DynamoDB, Lambda, API Gateway HTTP API | `GET /v1/lookup/{id}` with a plain English, per source explanation | **deployed**: 214,646 items loaded, lookups answered live |
| Analytics | Glue Data Catalog, Athena | Partition projected Parquet, a bytes scanned cutoff per query | **deployed**: a live query matched gold, 5,012 bytes scanned |
| Platform | Terraform, KMS, IAM, GitHub OIDC, CloudWatch, SNS, AWS Budgets | Least privilege roles, alarms, a monthly cost budget | **deployed** except the GitHub OIDC role (the account's SCP denies it); checkov 390 passed, 0 failed, 25 skipped with reasons (`runs/20261019T203857Z-checkov.json`) |
| CI | GitHub Actions | Core on Python 3.11 and 3.12, Spark on 3.5.3 and 4.0.1, dbt parse, Terraform | green; its first run found two bugs (incidents 9 and 10) |

**Cross engine conformance.** The same dbt models are built on DuckDB and on
Spark SQL, then every row of every mart is compared: 7 marts, 210,111 rows,
identical (`runs/20261019T203151Z-conformance.json`). Engine differences live
in `adapter.dispatch` macros (`warehouse/macros/cross_engine.sql`), never
inline in a model.

```
public sources -> S3 landing (immutable) -> bronze -> silver (SCD2, quality gate) -> gold (dbt)
                                                                                      |
   DynamoDB <- loader Lambda <- export: JSONL, Parquet, coverage <-------------------+
      |                                  |                                           |
 HTTP API lookup                   Athena and Glue                    EvidenceOps trust registry
```

Full diagram: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Decisions:
[docs/adr/](docs/adr/). Cloud specifics: [aws/README.md](aws/README.md) and
[databricks/README.md](databricks/README.md).

## Reproduce

```bash
make install       # venv: Spark 3.5.3, Delta 3.3.2, a bundled Java runtime, dbt for DuckDB and Spark
make pipeline      # fetch, bronze, silver, pmc, gold, export, findings; each step writes runs/*.json
make conformance   # gold on DuckDB and on Spark SQL, every row compared
make test          # 137 tests; the real data checks run when data/lake exists
```

The fetch lands 2.78 GB of raw bytes (39 Retraction Watch snapshots, NCBI's
PMC id map, 43,616 PMC metadata objects, OpenAlex, PubMed) and spends about
seven cents of the shared OpenAlex daily allowance. `make test-core` runs the
standard library core in under a second with no network.

To deploy, follow [aws/README.md](aws/README.md) (an AWS free tier account,
then `terraform apply`) and [databricks/README.md](databricks/README.md)
(Databricks Free Edition, then `databricks bundle deploy -t free`).

## Live deployment

On 2026-10-19 the stack was applied to a new AWS free plan account
(`us-east-2`) and the bundle to a Databricks Free Edition workspace. What ran:

- **AWS.** Terraform created the stack (everything except the GitHub OIDC
  deploy role, which the account's Organization denies by SCP). The fetch
  Lambda landed PubMed in 24.3 s with the same sha256 as the laptop's object;
  the OpenAlex container on Fargate landed 136,873 works in 709 calls; the
  nightly Step Functions workflow succeeded; the loader wrote 214,646 items
  for 92,097 documents into DynamoDB in about 55 s; the HTTP API answered
  lookups by DOI, PMID, and DOI URL; Athena returned gold's verdict counts.
- **Databricks.** The daily job ran every task green: setup, bronze, silver,
  the Lakeflow quality pipeline, dbt gold on the serverless SQL warehouse, and
  the export. Silver built the same history as the laptop (152,336 versions of
  73,334 records, the same snapshot quarantined with the same 5,520 lossy
  records), and after one fix the uncompressed export is byte identical to the
  laptop's, all 92,097 documents.

The live run found six bugs the local suite had not, each now fixed with a
test or a documented change:

11. **An SCP on free plan accounts denies `iam:CreateOpenIDConnectProvider`.**
    The GitHub deploy role is optional (`enable_github_deploy_role`).
12. **Terraform cannot read `aws login` credentials**; `aws/tf.sh` exports the
    session for one process.
13. **The loader refused the first export whole**: 126 verdicts were `ok`, a
    value outside the serving contract. Lone reinstatement records are now
    published as `reinstated`, and a test reads the loader's accepted values
    from its source.
14. **API Gateway collapses `//`**, so a DOI URL arrived as `https:/doi.org/...`
    and got a 400. Both forms are accepted, in Python and in Spark.
15. **The read only Fargate container could not write its run report.**
    Reports now go to `s3://<bucket>/runs/`.
16. **`F.first` chose a different PMID on Databricks than on the laptop** for 2
    papers whose Retraction Watch records disagree (3 papers in all). The
    choice is now deterministic: the retraction record's PMID first.

Live endpoint, while the stack is up:
`https://76gt9n1650.execute-api.us-east-2.amazonaws.com/v1/lookup/10.1056/NEJMoa2007621`

## What ran where

| | Executed live in the cloud | Executed on real data locally | Executed on synthetic data | Validated statically |
| --- | --- | --- | --- | --- |
| Fetchers, landing, CLI | Lambda and Fargate | yes | fake APIs | |
| Bronze and silver on Spark | Databricks job | yes | SCD2 lifecycle tests | |
| dbt gold | Databricks SQL warehouse | DuckDB and Spark SQL, compared row by row | | Snowflake, BigQuery: `dbt parse` |
| Lambda handlers and API | yes | | moto | |
| Terraform | applied | | `terraform test`, mock provider | fmt, validate, checkov |
| Databricks bundle and pipeline | deployed and run | | pipeline transforms, entry points | CLI schema |
| GitHub deploy role | blocked by the account's SCP | | | |

## Limitations

- **Year granularity for citations.** OpenAlex counts citations per year,
  from 2012. "After retraction" means a year strictly after the retraction
  year, which undercounts; papers retracted before 2011 are left out of the
  headline.
- **One hop identity.** A PMID is mapped to a DOI through one source. A paper
  linked only through a chain of mappings appears twice, which can undercount
  agreement and never overcounts it.
- **Positive sets only** for OpenAlex and PubMed. "Not flagged" there means
  "not in the retracted set", which mixes "fine" with "unknown to that source".
- **Monthly snapshots** before 2026-09-29, daily after. An edit made and
  reverted between two monthly snapshots is invisible.
- **The quality gate** compares a snapshot with current versions only, so a
  lossy value on a currently deleted record would pass.
- **PMC coverage is targeted:** the 43,616 articles other registries mention,
  not every article in the bucket. The full scan is designed as a Step
  Functions Distributed Map over the bucket's S3 Inventory and not built.

## Data sources and terms

- Retraction Watch Database, distributed by Crossref,
  `gitlab.com/crossref/retraction-watch-data`.
- OpenAlex (CC0), `api.openalex.org`.
- PubMed, through NCBI E-utilities.
- PMC Article Datasets, NIH NLM NCBI PubMed Central, accessed 2026-10-19 from
  `registry.opendata.aws/ncbi-pmc`, and `PMC-ids.csv.gz` from the NCBI FTP
  site. NLM is the source of these data. This project is not endorsed by NLM,
  and its outputs may not reflect the most current data NLM holds.

No raw data is stored in this repository; `make pipeline` fetches it. The exact
files the numbers above came from, all 39 Retraction Watch snapshots included,
are archived with checksums at https://doi.org/10.17605/OSF.IO/D7GSF. Code
under the MIT license.
