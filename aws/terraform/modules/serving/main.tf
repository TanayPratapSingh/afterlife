# Serving: the registry export becomes a DynamoDB table behind an HTTP API.
#
#   export/registry/as_of=D/registry.jsonl.gz --S3 event--> loader Lambda
#       --BatchWriteItem--> DynamoDB (one item per DOI, PMID, PMCID, OpenAlex id)
#   GET /v1/lookup/{id} --> API Lambda --GetItem--> DynamoDB
#
# DynamoDB rather than a warehouse query: every lookup is a single key read,
# on demand billing is per request, and an idle table costs only its storage.

locals {
  table_name  = "${var.name_prefix}-registry"
  loader_name = "${var.name_prefix}-load-registry"
  api_name    = "${var.name_prefix}-api"
  routes      = ["GET /v1/lookup/{id+}", "GET /v1/coverage", "GET /health"]
}

resource "aws_dynamodb_table" "registry" {
  name                        = local.table_name
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  deletion_protection_enabled = true

  attribute {
    name = "pk"
    type = "S"
  }

  # A paper that drops out of the export stops being refreshed and ages out.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
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

# Loader ---------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "loader" {
  name              = "/aws/lambda/${local.loader_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_sqs_queue" "dlq" {
  name                              = "${var.name_prefix}-serving-dlq"
  message_retention_seconds         = 1209600
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

resource "aws_iam_role" "loader" {
  name               = local.loader_name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "loader" {
  statement {
    sid       = "ReadExports"
    actions   = ["s3:GetObject"]
    resources = ["${var.bucket_arn}/export/registry/*"]
  }

  statement {
    # The loader probes for _coverage.json by listing its exact key, so the
    # prefix is always present and a plain StringLike suffices.
    sid       = "ListExports"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["export/registry/*"]
    }
  }

  statement {
    sid       = "WriteRegistry"
    actions   = ["dynamodb:BatchWriteItem", "dynamodb:PutItem", "dynamodb:GetItem"]
    resources = [aws_dynamodb_table.registry.arn]
  }

  statement {
    sid       = "UseLakeKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }

  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.loader.arn}:*"]
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
}

resource "aws_iam_role_policy" "loader" {
  name   = "load-registry"
  role   = aws_iam_role.loader.id
  policy = data.aws_iam_policy_document.loader.json
}

resource "aws_lambda_function" "loader" {
  #checkov:skip=CKV_AWS_117:Talks only to S3 and DynamoDB over TLS with IAM auth; a VPC would add endpoints or a NAT gateway for no isolation gain
  #checkov:skip=CKV_AWS_115:New accounts start with a concurrency quota of 10 and Lambda refuses reservations that leave fewer than 10 unreserved; one export a day triggers it
  #checkov:skip=CKV_AWS_272:Zips are built reproducibly and pinned by source_code_hash; Signer is listed as next work in aws/README.md
  function_name    = local.loader_name
  description      = "Loads export/registry/*.jsonl.gz into ${local.table_name}, one item per identifier alias"
  role             = aws_iam_role.loader.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "handler.handler"
  filename         = var.loader_zip_path
  source_code_hash = filebase64sha256(var.loader_zip_path)
  memory_size      = 1024 # the whole export is validated in memory before the first write
  timeout          = 900
  kms_key_arn      = var.kms_key_arn

  environment {
    variables = {
      TABLE_NAME    = aws_dynamodb_table.registry.name
      ITEM_TTL_DAYS = tostring(var.item_ttl_days)
      WRITE_WORKERS = "4"
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
    log_group             = aws_cloudwatch_log_group.loader.name
    application_log_level = "INFO"
    system_log_level      = "WARN"
  }

  depends_on = [aws_iam_role_policy.loader]
}

resource "aws_lambda_function_event_invoke_config" "loader" {
  function_name                = aws_lambda_function.loader.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 21600
}

resource "aws_lambda_permission" "s3_invokes_loader" {
  statement_id   = "AllowExportNotifications"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.loader.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = var.bucket_arn
  source_account = var.account_id
}

resource "aws_s3_bucket_notification" "exports" {
  bucket = var.bucket_id

  lambda_function {
    id                  = "registry-export"
    lambda_function_arn = aws_lambda_function.loader.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "export/registry/"
    filter_suffix       = ".jsonl.gz"
  }

  depends_on = [aws_lambda_permission.s3_invokes_loader]
}

# API ------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.api_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_iam_role" "api" {
  name               = local.api_name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "ReadRegistry"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.registry.arn]
  }

  statement {
    sid       = "UseLakeKey"
    actions   = ["kms:Decrypt"]
    resources = [var.kms_key_arn]
  }

  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"] # X-Ray has no resource level permissions for these actions
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "read-registry"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_lambda_function" "api" {
  #checkov:skip=CKV_AWS_117:Reads one DynamoDB table over TLS with IAM auth; a VPC would add an endpoint and cold start latency for no isolation gain
  #checkov:skip=CKV_AWS_115:New accounts start with a concurrency quota of 10 and Lambda refuses reservations that leave fewer than 10 unreserved; load is bounded by the stage throttle instead
  #checkov:skip=CKV_AWS_116:Invoked synchronously by API Gateway; a failed request returns an error to the caller, there is no event to dead letter
  #checkov:skip=CKV_AWS_272:Zips are built reproducibly and pinned by source_code_hash; Signer is listed as next work in aws/README.md
  function_name    = local.api_name
  description      = "Public lookup API over ${local.table_name}"
  role             = aws_iam_role.api.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "handler.handler"
  filename         = var.api_zip_path
  source_code_hash = filebase64sha256(var.api_zip_path)
  memory_size      = 256
  timeout          = 10
  kms_key_arn      = var.kms_key_arn

  environment {
    variables = {
      TABLE_NAME        = aws_dynamodb_table.registry.name
      CORS_ALLOW_ORIGIN = var.cors_allow_origins[0]
    }
  }

  tracing_config {
    mode = "Active"
  }

  logging_config {
    log_format            = "JSON"
    log_group             = aws_cloudwatch_log_group.api.name
    application_log_level = "INFO"
    system_log_level      = "WARN"
  }

  depends_on = [aws_iam_role_policy.api]
}

resource "aws_apigatewayv2_api" "this" {
  name          = local.api_name
  description   = "Is this DOI, PMID, PMCID, or OpenAlex id retracted, and according to whom?"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = var.cors_allow_origins
    allow_methods = ["GET"]
    allow_headers = ["content-type"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 10000
}

resource "aws_apigatewayv2_route" "this" {
  #checkov:skip=CKV_AWS_309:A public read only lookup over public data; abuse is bounded by stage throttling and the budget alarm, and auth would defeat the purpose
  for_each           = toset(local.routes)
  api_id             = aws_apigatewayv2_api.this.id
  route_key          = each.value
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "NONE"
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${local.api_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = var.throttle_burst
    throttling_rate_limit  = var.throttle_rate
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId          = "$context.requestId"
      requestTime        = "$context.requestTime"
      sourceIp           = "$context.identity.sourceIp"
      routeKey           = "$context.routeKey"
      path               = "$context.path"
      status             = "$context.status"
      responseLength     = "$context.responseLength"
      integrationLatency = "$context.integrationLatency"
      integrationError   = "$context.integrationErrorMessage"
    })
  }
}

resource "aws_lambda_permission" "apigw_invokes_api" {
  statement_id  = "AllowHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}
