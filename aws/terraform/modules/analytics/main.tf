# Ad hoc SQL over the registry exports with Athena, no cluster to run.
#
# The table reads parquet the exporter writes next to the JSONL:
#
#   export/registry/as_of=YYYY-MM-DD/parquet/*.parquet
#
# in its own subdirectory, because Athena reads every file under a partition
# location and would fail on the .jsonl.gz (files starting with _ or . are
# skipped, so _coverage.json is not a problem). Partition projection computes
# partitions from the as_of range, so there is no crawler and no MSCK REPAIR.
#
# The parquet files must not contain an as_of column: it is the partition key,
# and Athena rejects a table whose data columns repeat a partition column.

locals {
  database = replace(var.name_prefix, "-", "_")
  location = "s3://${var.bucket_id}/export/registry"
}

resource "aws_glue_catalog_database" "this" {
  name        = local.database
  description = "afterlife registry exports"
}

resource "aws_glue_catalog_table" "registry" {
  name          = "registry"
  database_name = aws_glue_catalog_database.this.name
  description   = "One row per paper per export date. Contract in aws/README.md."
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL                         = "TRUE"
    classification                   = "parquet"
    "projection.enabled"             = "true"
    "projection.as_of.type"          = "date"
    "projection.as_of.format"        = "yyyy-MM-dd"
    "projection.as_of.range"         = "${var.projection_start},NOW"
    "projection.as_of.interval"      = "1"
    "projection.as_of.interval.unit" = "DAYS"
    "storage.location.template"      = "${local.location}/as_of=$${as_of}/parquet/"
  }

  partition_keys {
    name = "as_of"
    type = "string"
  }

  storage_descriptor {
    location      = "${local.location}/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    columns {
      name = "paper_key"
      type = "string"
    }
    columns {
      name = "ids"
      type = "struct<doi:string,pmid:string,pmcid:string,openalex:string>"
    }
    columns {
      name = "title"
      type = "string"
    }
    columns {
      name    = "verdict"
      type    = "string"
      comment = "retracted, concern, corrected, reinstated, or disputed"
    }
    columns {
      name    = "flags"
      type    = "struct<retraction_watch:string,openalex:boolean,pubmed:boolean,pmc:boolean>"
      comment = "null means the source has no record of the paper"
    }
    columns {
      name = "retraction_watch"
      type = "struct<status:string,first_retraction_date:string,record_ids:array<bigint>,natures:array<string>,reasons:array<string>>"
    }
    columns {
      name = "agreement"
      type = "struct<flagging:int,observed:int>"
    }
  }
}

resource "aws_athena_workgroup" "this" {
  name        = var.name_prefix
  description = "afterlife analysis; every query is capped at bytes_scanned_cutoff_per_query"
  state       = "ENABLED"

  configuration {
    # Clients cannot override the output location, encryption, or the cutoff.
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = var.bytes_scanned_cutoff

    engine_version {
      selected_engine_version = "Athena engine version 3"
    }

    result_configuration {
      output_location       = "s3://${var.bucket_id}/athena-results/"
      expected_bucket_owner = var.account_id

      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = var.kms_key_arn
      }
    }
  }
}

resource "aws_athena_named_query" "rw_only" {
  name        = "retracted-in-retraction-watch-only"
  workgroup   = aws_athena_workgroup.this.name
  database    = aws_glue_catalog_database.this.name
  description = "Papers Retraction Watch lists as retracted that PubMed knows but does not flag. Pass the export date: --execution-parameters \"'2026-10-19'\"."
  query       = <<-SQL
    SELECT paper_key, ids.doi, ids.pmid, retraction_watch.first_retraction_date
    FROM registry
    WHERE as_of = ?
      AND flags.retraction_watch = 'retracted'
      AND flags.pubmed = false
    ORDER BY retraction_watch.first_retraction_date
  SQL
}

resource "aws_athena_named_query" "agreement" {
  name        = "agreement-by-verdict"
  workgroup   = aws_athena_workgroup.this.name
  database    = aws_glue_catalog_database.this.name
  description = "How many registries flag each paper, by combined verdict, for one export date passed as the parameter."
  query       = <<-SQL
    SELECT verdict, agreement.flagging, agreement.observed, count(*) AS papers
    FROM registry
    WHERE as_of = ?
    GROUP BY 1, 2, 3
    ORDER BY 1, 2, 3
  SQL
}
