# The fetch Lambda: Retraction Watch and PubMed, one invocation per source.

locals {
  fetch_name          = "${var.name_prefix}-fetch"
  openalex_secret_arn = var.openalex_api_key_enabled ? aws_secretsmanager_secret.openalex[0].arn : ""
}

resource "aws_cloudwatch_log_group" "fetch" {
  name              = "/aws/lambda/${local.fetch_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_sqs_queue" "dlq" {
  name                              = "${var.name_prefix}-ingest-dlq"
  message_retention_seconds         = 1209600 # 14 days, the SQS maximum
  kms_master_key_id                 = var.kms_key_arn
  kms_data_key_reuse_period_seconds = 3600
}

data "aws_iam_policy_document" "dlq" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [aws_sqs_queue.dlq.arn]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_sqs_queue_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  policy    = data.aws_iam_policy_document.dlq.json
}

resource "aws_secretsmanager_secret" "openalex" {
  #checkov:skip=CKV2_AWS_57:A third party API key issued by OpenAlex; a rotation Lambda cannot mint a new one, so it is rotated at OpenAlex by hand
  count                   = var.openalex_api_key_enabled ? 1 : 0
  name                    = "${var.name_prefix}/openalex-api-key"
  description             = "OpenAlex API key. The value is set with the AWS CLI so it never enters Terraform state."
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = 7
}

# Permissions shared by every fetcher, on Lambda or on Fargate: read and
# create objects under landing/, and nothing else in the bucket.
data "aws_iam_policy_document" "landing_writer" {
  statement {
    sid       = "ReadAndCreateLandedObjects"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.bucket_arn}/landing/*"]
  }

  statement {
    sid       = "ListLandingPrefix"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arn]
    # IfExists, not plain StringLike: S3 checks ListBucket again when a HEAD
    # or GET names a missing key, to choose between 404 and 403, and that
    # check carries no s3:prefix. With plain StringLike the landing code's
    # existence check would get 403 for every new snapshot instead of 404.
    condition {
      test     = "StringLikeIfExists"
      variable = "s3:prefix"
      values   = ["landing/*"]
    }
  }

  # Run reports from the container. The first Fargate run fetched and landed
  # OpenAlex, then died writing its report to a read only root filesystem;
  # a report kept in S3 also outlives the task.
  statement {
    sid       = "CreateRunReports"
    actions   = ["s3:PutObject"]
    resources = ["${var.bucket_arn}/runs/*"]
  }

  statement {
    sid       = "UseLakeKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role" "fetch" {
  name               = local.fetch_name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "fetch" {
  source_policy_documents = [data.aws_iam_policy_document.landing_writer.json]

  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.fetch.arn}:*"]
  }

  statement {
    sid       = "DeadLetter"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.dlq.arn]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"] # X-Ray has no resource level permissions for these actions
  }

  # On Fargate the execution role injects the key; here the code reads it.
  dynamic "statement" {
    for_each = var.openalex_api_key_enabled ? [1] : []
    content {
      sid       = "ReadOpenAlexKey"
      actions   = ["secretsmanager:GetSecretValue"]
      resources = [local.openalex_secret_arn]
    }
  }
}

resource "aws_iam_role_policy" "fetch" {
  name   = "fetch"
  role   = aws_iam_role.fetch.id
  policy = data.aws_iam_policy_document.fetch.json
}

resource "aws_lambda_function" "fetch" {
  #checkov:skip=CKV_AWS_117:Fetchers need the public internet (GitLab, NCBI, OpenAlex); a VPC would need a NAT gateway, billed every hour whether or not a fetch runs. No inbound ports, AWS calls are TLS with IAM auth
  #checkov:skip=CKV_AWS_115:New accounts start with a concurrency quota of 10 and Lambda refuses reservations that leave fewer than 10 unreserved; this function runs twice a day and conditional writes make concurrent runs safe
  #checkov:skip=CKV_AWS_272:Zips are built reproducibly and pinned by source_code_hash; Signer adds a signing profile per function and is listed as next work in aws/README.md
  function_name    = local.fetch_name
  description      = "Lands the newest Retraction Watch or PubMed snapshot in s3://${var.bucket_id}/landing/"
  role             = aws_iam_role.fetch.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "handler.handler"
  filename         = var.fetch_zip_path
  source_code_hash = filebase64sha256(var.fetch_zip_path)
  memory_size      = 1024 # the 67 MB Retraction Watch CSV is held, decoded, and compressed in memory
  timeout          = 300
  kms_key_arn      = var.kms_key_arn

  environment {
    variables = {
      LANDING_BUCKET      = var.bucket_id
      OPENALEX_BUDGET_USD = tostring(var.openalex_budget_usd)
      OPENALEX_SECRET_ARN = local.openalex_secret_arn
    }
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.dlq.arn
  }

  tracing_config {
    mode = "Active"
  }

  logging_config {
    log_format            = "JSON"
    log_group             = aws_cloudwatch_log_group.fetch.name
    application_log_level = "INFO"
    system_log_level      = "WARN"
  }

  depends_on = [aws_iam_role_policy.fetch]
}

resource "aws_lambda_function_event_invoke_config" "fetch" {
  function_name = aws_lambda_function.fetch.function_name
  # Scheduler invokes asynchronously. One Lambda retry after a failure, then
  # the event goes to the DLQ and the DLQ depth alarm fires.
  maximum_retry_attempts       = 1
  maximum_event_age_in_seconds = 3600
}
