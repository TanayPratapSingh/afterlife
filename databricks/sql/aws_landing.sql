-- One time setup for the aws target, run by a metastore admin before the
-- first `databricks bundle deploy -t aws`. No job runs this file, and it does
-- not apply to Free Edition, which has no external locations.
--
-- It points Unity Catalog at the landing bucket the Terraform in aws/
-- creates, so the job reads the same objects the fetch Lambdas write, through
-- a volume path, with no AWS keys in any job.
--
-- Replace before running:
--   <account>  the AWS account id in the bucket name s3://afterlife-landing-<account>
--   <sp>       the application id of the service principal in var.service_principal
--
-- Prerequisite: a storage credential named afterlife_landing_role for the IAM
-- role Databricks assumes to reach the bucket. AWS IAM role credentials are
-- created in Catalog Explorer, the API, or Terraform, not in SQL.
--
-- Not executed: written against the documented syntax, never run.

CREATE EXTERNAL LOCATION IF NOT EXISTS afterlife_landing
URL 's3://afterlife-landing-<account>/'
WITH (STORAGE CREDENTIAL afterlife_landing_role)
COMMENT 'The afterlife landing bucket, written by the fetch Lambdas in aws/.';

CREATE CATALOG IF NOT EXISTS afterlife
COMMENT 'afterlife: a cross source retraction registry. Layers: landing, bronze, silver, gold, publication, ops.';

CREATE SCHEMA IF NOT EXISTS afterlife.landing
COMMENT 'Raw source bytes exactly as fetched. Append only: never rewritten, never deleted (ADR 0004).';

-- The whole bucket: landing/ (read by bronze) and export/ (written by the
-- export task, where registry/*.jsonl.gz triggers aws/lambdas/load_registry).
CREATE EXTERNAL VOLUME IF NOT EXISTS afterlife.landing.s3
LOCATION 's3://afterlife-landing-<account>/'
COMMENT 'The landing zone and the export prefix on S3: landing_root and exports_root of the aws target.';

-- The job and the pipeline run as the service principal; it creates every
-- other schema, table, and volume through setup.sql and the jobs.
GRANT USE CATALOG, USE SCHEMA, SELECT, MODIFY, CREATE SCHEMA, CREATE TABLE,
      CREATE MATERIALIZED VIEW, CREATE VOLUME, READ VOLUME, WRITE VOLUME
ON CATALOG afterlife TO `<sp>`;
