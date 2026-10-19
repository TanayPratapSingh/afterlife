-- afterlife: the Unity Catalog objects every layer writes into, created only
-- if absent. Safe to run on every job run.
--
-- Run by the `setup` task of afterlife_daily, a SQL file task on the SQL
-- warehouse, with one task parameter: catalog. The name arrives as the named
-- parameter marker :catalog rather than as ${var.catalog}, because a bundle
-- interpolates variables in its YAML only, never inside the files it deploys.
-- A marker cannot stand where an object name goes, so it is wrapped in
-- IDENTIFIER(), and USE CATALOG lets every later statement stay unqualified.
-- IDENTIFIER therefore appears exactly twice in this file.
--
-- Not executed anywhere yet: checked statically only (databricks/README.md).
-- If a runtime rejects IDENTIFIER in either statement, replace both with the
-- literal catalog name; nothing else here depends on the parameter.

CREATE CATALOG IF NOT EXISTS IDENTIFIER(:catalog)
COMMENT 'afterlife: a cross source retraction registry. Layers: landing, bronze, silver, gold, publication, ops.';

USE CATALOG IDENTIFIER(:catalog);

CREATE SCHEMA IF NOT EXISTS landing
COMMENT 'Raw source bytes exactly as fetched, in the files volume. Append only: never rewritten, never deleted (ADR 0004).';

CREATE SCHEMA IF NOT EXISTS bronze
COMMENT 'One Delta table per source: landed bytes parsed, nothing inferred. Every row carries its object key, sha256, encoding, and ingest time.';

CREATE SCHEMA IF NOT EXISTS silver
COMMENT 'Conformed, typed, deduplicated. rw_history is an SCD2 history over every replayed Retraction Watch snapshot.';

CREATE SCHEMA IF NOT EXISTS gold
COMMENT 'dbt marts from warehouse/: registry, registry_agreement, retraction_lag, afterlife_citations, reason_trends, record_mutability.';

CREATE SCHEMA IF NOT EXISTS publication
COMMENT 'Owned by the registry_quality pipeline: contract checked copies of silver and the results of every expectation.';

CREATE SCHEMA IF NOT EXISTS ops
COMMENT 'Operational data: job run reports, exports, the pipeline event log.';

CREATE VOLUME IF NOT EXISTS landing.files
COMMENT 'The landing zone on Free Edition: landing/<source>/snapshot_date=YYYY-MM-DD/<object>.gz plus _manifest.json.';

CREATE VOLUME IF NOT EXISTS ops.runs
COMMENT 'One JSON run report per job task run, written by the afterlife_job entry point.';

CREATE VOLUME IF NOT EXISTS ops.exports
COMMENT 'Registry documents (jsonl.gz, parquet), coverage, and EvidenceOps trust lines.';

-- Grants. Examples for a paid workspace with account groups named
-- afterlife-readers and afterlife-engineers. Free Edition has no account
-- console and no groups to grant to, so they stay commented out.
--
-- Readers see the published layers and nothing upstream of them:
--   GRANT USE CATALOG ON CATALOG afterlife TO `afterlife-readers`;
--   GRANT USE SCHEMA, SELECT ON SCHEMA afterlife.gold TO `afterlife-readers`;
--   GRANT USE SCHEMA, SELECT ON SCHEMA afterlife.publication TO `afterlife-readers`;
--   GRANT READ VOLUME ON VOLUME afterlife.ops.exports TO `afterlife-readers`;
--
-- Engineers can rerun any layer by hand. Nobody but the job writes landing:
--   GRANT USE CATALOG, USE SCHEMA, SELECT, MODIFY, CREATE TABLE
--     ON CATALOG afterlife TO `afterlife-engineers`;
--   GRANT READ VOLUME ON VOLUME afterlife.landing.files TO `afterlife-engineers`;
--
-- Table properties. The jobs create their tables, not this file, because a
-- table's schema belongs to the code that writes it (silver overwrites with
-- overwriteSchema). Guidance for the tables that need more than defaults:
--
-- * Change data feed on silver.rw_history. silver.py sets
--   delta.enableChangeDataFeed when it creates the table (confirmed on the
--   local lake with DESCRIBE DETAIL). For a copy created any other way:
--     ALTER TABLE silver.rw_history SET TBLPROPERTIES (delta.enableChangeDataFeed = true);
--   table_changes('silver.rw_history', 1) then lists every version a MERGE
--   cut or closed: the audit trail of the SCD2 history at no extra cost.
--
-- * Liquid clustering on the large tables. The MERGE in silver.py matches on
--   record_id and filters on is_current:
--     ALTER TABLE silver.rw_history CLUSTER BY (record_id, is_current);
--   A --silver-rebuild recreates the table, so reapply it after one.
--   bronze.retraction_watch is partitioned by _snapshot_date in bronze.py, and
--   a table cannot be both partitioned and clustered, so moving it is a
--   rewrite, and bronze._append must stop calling partitionBy first:
--     CREATE OR REPLACE TABLE bronze.retraction_watch CLUSTER BY (_snapshot_date)
--       AS SELECT * FROM bronze.retraction_watch;
--   bronze.pmc_ids is joined by doi and by pmid in silver.build_pmc_targets:
--     ALTER TABLE bronze.pmc_ids CLUSTER BY (doi, pmid);
--
-- * No OPTIMIZE or VACUUM job is scheduled. Managed tables get predictive
--   optimization where the account has it enabled; check the catalog's
--   Details tab before adding maintenance of your own.
