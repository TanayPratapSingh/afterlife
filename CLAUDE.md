# afterlife: working context

## What this is

A cross source retraction registry built as a lakehouse. Four public
registries say whether a paper was retracted (Retraction Watch via Crossref,
OpenAlex, PubMed, PMC) and they disagree. This project lands each one
immutably, historizes Retraction Watch across its daily snapshots, resolves
identity across DOI, PMID, and PMCID, measures how far the registries agree,
and serves the answer through an API that never says "ok", only "no registry
here flags this, as of this date".

It is the upstream half of EvidenceOps (a separate, unpublished repo; locally
`../evidenceops`): its export
`data/export/evidenceops/trust_registry.jsonl` is in EvidenceOps' TrustRecord
format.

Read `README.md`, `docs/ARCHITECTURE.md`, and `docs/adr/` before structural
changes. The ADRs record decisions that should not be silently reversed.

## What is verified and what is not

Executed on real data, on a laptop: every fetcher, the landing zone, bronze,
silver (39 replayed snapshots), the PMC enrichment, dbt on DuckDB and on Spark
SQL, the conformance diff, export, findings, and the test suite.

Written and validated statically only: everything under `aws/` (moto tests,
terraform validate, checkov) and `databricks/` (YAML structure tests, local
unit tests of pipeline functions). Neither has touched a real cloud account.
The dbt profiles for Databricks, Snowflake, and BigQuery compile; they have
not run against a live warehouse. See the tables in `aws/README.md` and
`databricks/README.md`.

## House rules

1. **Never invent a metric.** Every number in README, RESULTS, or a resume
   traces to `runs/*-findings.json` or another `runs/*.json` report, named in
   the same sentence or table.
2. **Never tune a threshold to make a result look better.**
   `LOSSY_RECORDS_MAX` in silver.py was set from the measured per snapshot
   lossy counts (zero everywhere except 2026-10-15). Changing it needs the
   same kind of evidence.
3. **No em dashes** anywhere. No hyphens in ordinary compound phrases in
   prose ("real time", "end to end", "free tier").
4. **The landing zone is append only.** Never delete or rewrite under
   `data/landing/` to "fix" a source. Quarantine downstream instead (ADR 0004).
5. **Report upstream problems as findings.** The cp1252 snapshot, the blank
   rows, the taxonomy relabels are results, not noise to hide.
6. **The core stays standard library.** `afterlife/core` and
   `afterlife/sources` import nothing outside the standard library, so they
   run unchanged in Lambda.
7. **Python reference, Spark fast path.** Any Spark expression that
   reimplements logic in `afterlife/core` must be covered by an equivalence
   test over the real landed data (`tests/test_spark.py`).

## Commands

```bash
make install        # venv with Spark, Delta, a bundled JRE, dbt (duckdb and spark)
make test-core      # under a second, no Spark
make test           # everything; real lake checks run when data/lake exists
make pipeline       # fetch, bronze, silver, pmc, gold, export, findings
make conformance    # gold on DuckDB and Spark SQL, every row compared
make aws-build tf-validate
.venv/bin/afterlife audit sheet     # labels.xlsx from labels.csv (needs the audit extra)
.venv/bin/afterlife audit score     # hand audit progress; RESULTS.md section 9 once a score run exists
.venv/bin/python paper/figures.py   # paper figures from the newest findings run
```

## Hand audit and paper

`audit/labels.csv` is labelled by Tanay by hand, never by a model (a model
labelled audit measures nothing). Never open or print the key in
`runs/*-audit-sample.json` to Tanay before labelling is done, and never redraw
the sample: `afterlife audit sample` refuses to overwrite the sheet on
purpose. Codebook changes after labelling starts go in its change log.
`paper/draft.md` marks each number with its findings key; regenerate numbers,
never type them.

## Known limits

- OpenAlex requires an API key for cursor paging. Without one the fetcher
  partitions by publication date; 97 of 136,970 works fell outside the date
  slices and are reported, not fetched. A free key (Tanay must request it
  himself) enables cursor mode via `OPENALEX_API_KEY`.
- OpenAlex `counts_by_year` starts in 2012, so citation afterlife numbers for
  older retractions are lower bounds (`window_complete = false`).
- The quality gate compares a snapshot to current versions only; a lossy
  value on a record that is currently deleted would not trip it.
- Identity resolution is one hop. Papers linked only through a chain of two
  mappings appear as two registry rows (undercounts agreement, never
  overcounts).
