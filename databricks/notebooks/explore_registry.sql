-- Databricks notebook source
-- MAGIC %md
-- MAGIC # afterlife: explore the registry
-- MAGIC
-- MAGIC Four public registries say whether a paper was retracted, and they disagree. This notebook walks the layers the `afterlife_daily` job builds, from the published answer down to the SCD2 history that explains it. It only reads, so it runs on serverless compute or on the SQL warehouse.
-- MAGIC
-- MAGIC Deployed with a catalog other than `afterlife`? Change the next cell and the volume path in the last section.

-- COMMAND ----------

USE CATALOG afterlife;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 1. The answer: one row per paper, with a verdict
-- MAGIC
-- MAGIC `gold.registry` is the dbt mart the API serves. A paper is `disputed` when Retraction Watch has a record of it that is not a standing retraction while another registry flags it as retracted.

-- COMMAND ----------

SELECT verdict, count(*) AS papers
FROM gold.registry
GROUP BY verdict
ORDER BY papers DESC;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 2. How far the registries agree
-- MAGIC
-- MAGIC `n_flagging` counts the registries that flag a paper. A paper only one registry flags is a paper a reader relying on any other registry would cite without warning.

-- COMMAND ----------

SELECT n_flagging, count(*) AS papers
FROM gold.registry
GROUP BY n_flagging
ORDER BY n_flagging;

-- COMMAND ----------

SELECT rw_status, flag_openalex, flag_pubmed, flag_pmc, count(*) AS papers
FROM gold.registry
GROUP BY ALL
ORDER BY papers DESC
LIMIT 25;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 3. Disputed papers, most cited first

-- COMMAND ----------

SELECT paper_key, title, rw_status, flag_openalex, flag_pubmed, flag_pmc, cited_by_count
FROM gold.registry
WHERE verdict = 'disputed'
ORDER BY cited_by_count DESC NULLS LAST
LIMIT 25;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 4. The publication contract
-- MAGIC
-- MAGIC The `registry_quality` pipeline publishes `publication.notices` and `publication.paper_status` only after its expectations run: a null `paper_key` fails the update, a malformed DOI drops the row, a future dated notice only warns. `expectation_results` holds the counts, measured before anything is dropped.

-- COMMAND ----------

SELECT dataset, expectation, action, total_rows, failing_rows, passing_rows, measured_at
FROM publication.expectation_results
ORDER BY dataset, expectation;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC The same counts as the pipeline runtime records them, per update, in the event log the bundle publishes to `ops`.

-- COMMAND ----------

SELECT timestamp, origin.flow_name, details:flow_progress.data_quality.expectations AS expectations
FROM ops.registry_quality_event_log
WHERE event_type = 'flow_progress'
  AND details:flow_progress.data_quality IS NOT NULL
ORDER BY timestamp DESC
LIMIT 25;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 5. Records that changed after they were published
-- MAGIC
-- MAGIC `silver.rw_changes` compares the meaning of each version with the one before it, so a formatting change upstream does not count as an edit.

-- COMMAND ----------

SELECT changed_column, count(*) AS edits
FROM silver.rw_changes
LATERAL VIEW explode(changed_columns) c AS changed_column
WHERE change_type = 'update'
GROUP BY changed_column
ORDER BY edits DESC;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 6. SCD2: every version of the most edited record

-- COMMAND ----------

WITH most_edited AS (
  SELECT record_id
  FROM silver.rw_history
  GROUP BY record_id
  ORDER BY count(*) DESC, record_id
  LIMIT 1
)
SELECT record_id, valid_from, valid_to, is_current, change_type, end_reason,
       retraction_nature, retraction_date, original_paper_doi, reason
FROM silver.rw_history
WHERE record_id IN (SELECT record_id FROM most_edited)
ORDER BY valid_from;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 7. Snapshots the quality gate refused
-- MAGIC
-- MAGIC A snapshot that shows the lossy transcoding signature is recorded here with its evidence and never merged. The landed bytes are untouched.

-- COMMAND ----------

SELECT snapshot_date, reason, lossy_records
FROM silver.rw_quarantine
ORDER BY snapshot_date;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 8. Delta: the table history and the change data feed

-- COMMAND ----------

DESCRIBE HISTORY silver.rw_history LIMIT 10;

-- COMMAND ----------

SELECT _change_type, count(*) AS row_changes
FROM table_changes('silver.rw_history', 1)
GROUP BY _change_type
ORDER BY _change_type;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 9. Run reports
-- MAGIC
-- MAGIC Every job task writes a JSON report to the `ops.runs` volume, including the ones that failed a contract.

-- COMMAND ----------

SELECT command, status, seconds, finished_at, error
FROM read_files('/Volumes/afterlife/ops/runs/', format => 'json', multiLine => true)
ORDER BY finished_at DESC
LIMIT 25;

-- COMMAND ----------

SHOW TABLES IN gold;
