# Daily orchestration, in one of two shapes (var.orchestrator):
#
#   step_functions  one schedule starts afterlife-daily, which runs the three
#                   fetches in parallel, then (if configured) the Databricks
#                   job, but only when every fetch landed.
#   scheduler       one schedule per source, straight at Lambda and ECS. No
#                   Databricks trigger; the lake job runs on its own schedule.
#
# Both shapes exist in code so the state machine can be bypassed without a
# redeploy of the fetchers. Never both at once: each source would run twice
# (harmless, since landing refuses duplicates, but it costs a second OpenAlex
# fetch).

locals {
  direct_schedules   = var.orchestrator == "scheduler"
  databricks_enabled = var.databricks_host != ""
  state_machine_name = "${var.name_prefix}-daily"
  state_machine_arn  = "arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${local.state_machine_name}"
  run_now_endpoint   = "https://${var.databricks_host}/api/2.1/jobs/run-now"

  lambda_sources = {
    "retraction-watch" = "RetractionWatch"
    "pubmed"           = "PubMed"
  }

  # Errors that a retry cannot fix: a snapshot whose bytes changed under the
  # same date (ImmutableWriteError) and published schema drift (ValueError)
  # are findings to read, not transients. MaxAttempts 0 means never retry.
  lambda_retry = [
    { ErrorEquals = ["ImmutableWriteError", "ValueError"], MaxAttempts = 0 },
    {
      ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"]
      IntervalSeconds = 2
      MaxAttempts     = 6
      BackoffRate     = 2
      JitterStrategy  = "FULL"
    },
    { ErrorEquals = ["States.ALL"], IntervalSeconds = 120, MaxAttempts = 2, BackoffRate = 2 },
  ]

  lambda_branches = [for source, state in local.lambda_sources : {
    StartAt = state
    States = {
      (state) = {
        Type     = "Task"
        Resource = "arn:${var.partition}:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.fetch.arn
          Payload      = { source = source }
        }
        ResultSelector = { source = source, ok = true, "summary.$" = "$.Payload" }
        Retry          = local.lambda_retry
        Catch          = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "${state}Failed" }]
        End            = true
      }
      "${state}Failed" = {
        Type       = "Pass"
        Parameters = { source = source, ok = false, "error.$" = "$.error" }
        End        = true
      }
    }
  }]

  openalex_branch = {
    StartAt = "OpenAlex"
    States = {
      OpenAlex = {
        Type     = "Task"
        Resource = "arn:${var.partition}:states:::ecs:runTask.sync"
        Parameters = {
          Cluster         = aws_ecs_cluster.this.arn
          TaskDefinition  = aws_ecs_task_definition.openalex.arn
          LaunchType      = "FARGATE"
          PlatformVersion = "LATEST"
          PropagateTags   = "TASK_DEFINITION"
          NetworkConfiguration = {
            AwsvpcConfiguration = {
              Subnets        = local.subnet_ids
              SecurityGroups = [aws_security_group.openalex.id]
              AssignPublicIp = "ENABLED"
            }
          }
        }
        TimeoutSeconds = 3600
        ResultSelector = { source = "openalex", ok = true, "task_arn.$" = "$.TaskArn" }
        # Only ECS API errors are retried. A container that exited non zero
        # already spent its OpenAlex budget, and the CLI path has no "landed
        # today" check, so a blind retry would pay for the fetch twice.
        Retry = [{ ErrorEquals = ["ECS.AmazonECSException"], IntervalSeconds = 60, MaxAttempts = 3, BackoffRate = 2 }]
        Catch = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "OpenAlexFailed" }]
        End   = true
      }
      OpenAlexFailed = {
        Type       = "Pass"
        Parameters = { source = "openalex", ok = false, "error.$" = "$.error" }
        End        = true
      }
    }
  }

  databricks_states = {
    for name, state in {
      TriggerDatabricks = {
        Type     = "Task"
        Resource = "arn:${var.partition}:states:::http:invoke"
        Parameters = {
          ApiEndpoint    = local.run_now_endpoint
          Method         = "POST"
          Authentication = { ConnectionArn = local.databricks_enabled ? aws_cloudwatch_event_connection.databricks[0].arn : "" }
          RequestBody    = { job_id = var.databricks_job_id }
        }
        ResultSelector = { "run_id.$" = "$.ResponseBody.run_id" }
        ResultPath     = "$.databricks"
        Retry = [{
          ErrorEquals     = ["States.Http.StatusCode.429", "States.Http.StatusCode.500", "States.Http.StatusCode.502", "States.Http.StatusCode.503", "States.Http.StatusCode.504"]
          IntervalSeconds = 30
          MaxAttempts     = 3
          BackoffRate     = 2
        }]
        Next = "Done"
      }
    } : name => state if local.databricks_enabled
  }

  definition = {
    Comment = "Land Retraction Watch, PubMed, and OpenAlex in parallel; start the Databricks job only if all three landed."
    StartAt = "Fetch"
    States = merge(
      {
        Fetch = {
          Type       = "Parallel"
          Branches   = concat(local.lambda_branches, [local.openalex_branch])
          ResultPath = "$.fetch"
          Next       = "AllLanded"
        }
        AllLanded = {
          Type = "Choice"
          Choices = [{
            And  = [for i in range(3) : { Variable = "$.fetch[${i}].ok", BooleanEquals = true }]
            Next = local.databricks_enabled ? "TriggerDatabricks" : "Done"
          }]
          Default = "FetchFailed"
        }
        FetchFailed = {
          Type  = "Fail"
          Error = "FetchFailed"
          Cause = "At least one source did not land; the per source results are in the Fetch state output. The Databricks job was not started."
        }
        Done = { Type = "Succeed" }
      },
      local.databricks_states,
    )
  }
}

# Databricks connection ------------------------------------------------------

resource "aws_cloudwatch_event_connection" "databricks" {
  count              = local.databricks_enabled ? 1 : 0
  name               = "${var.name_prefix}-databricks"
  description        = "Bearer token for the Databricks Jobs API. EventBridge keeps it in a Secrets Manager secret it manages."
  authorization_type = "API_KEY"

  auth_parameters {
    api_key {
      key   = "Authorization"
      value = "Bearer ${var.databricks_token}"
    }
  }

  lifecycle {
    precondition {
      condition     = var.databricks_token != "" && var.databricks_job_id > 0
      error_message = "databricks_host is set, so databricks_token and databricks_job_id are required."
    }
  }
}

# State machine --------------------------------------------------------------

resource "aws_cloudwatch_log_group" "state_machine" {
  name              = "/aws/vendedlogs/states/${local.state_machine_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

data "aws_iam_policy_document" "states_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role" "state_machine" {
  name               = local.state_machine_name
  assume_role_policy = data.aws_iam_policy_document.states_assume.json
}

data "aws_iam_policy_document" "state_machine" {
  statement {
    sid       = "InvokeFetch"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.fetch.arn, "${aws_lambda_function.fetch.arn}:*"]
  }

  statement {
    sid       = "RunOpenAlexTask"
    actions   = ["ecs:RunTask"]
    resources = ["arn:${var.partition}:ecs:${var.region}:${var.account_id}:task-definition/${local.task_family}:*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.this.arn]
    }
  }

  statement {
    sid       = "WatchOpenAlexTask"
    actions   = ["ecs:StopTask", "ecs:DescribeTasks"]
    resources = ["arn:${var.partition}:ecs:${var.region}:${var.account_id}:task/${aws_ecs_cluster.this.name}/*"]
  }

  statement {
    sid       = "PassTaskRoles"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.task.arn, aws_iam_role.task_execution.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    # The .sync integration waits on ECS task events through a managed rule.
    sid       = "SyncRule"
    actions   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
    resources = ["arn:${var.partition}:events:${var.region}:${var.account_id}:rule/StepFunctionsGetEventsForECSTaskRule"]
  }

  statement {
    sid = "VendedLogDelivery"
    actions = [
      "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
    ]
    resources = ["*"] # log delivery actions have no resource level permissions
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
    resources = ["*"] # X-Ray has no resource level permissions for these actions
  }

  dynamic "statement" {
    for_each = local.databricks_enabled ? [1] : []
    content {
      sid       = "CallDatabricksRunNow"
      actions   = ["states:InvokeHTTPEndpoint"]
      resources = [local.state_machine_arn]
      condition {
        test     = "StringEquals"
        variable = "states:HTTPMethod"
        values   = ["POST"]
      }
      condition {
        test     = "StringLike"
        variable = "states:HTTPEndpoint"
        values   = [local.run_now_endpoint]
      }
    }
  }

  dynamic "statement" {
    for_each = local.databricks_enabled ? [1] : []
    content {
      sid       = "UseDatabricksConnection"
      actions   = ["events:RetrieveConnectionCredentials"]
      resources = [aws_cloudwatch_event_connection.databricks[0].arn]
    }
  }

  dynamic "statement" {
    for_each = local.databricks_enabled ? [1] : []
    content {
      sid       = "ReadDatabricksConnectionSecret"
      actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
      resources = [aws_cloudwatch_event_connection.databricks[0].secret_arn]
    }
  }
}

resource "aws_iam_role_policy" "state_machine" {
  name   = "orchestrate"
  role   = aws_iam_role.state_machine.id
  policy = data.aws_iam_policy_document.state_machine.json
}

resource "aws_sfn_state_machine" "daily" {
  name       = local.state_machine_name
  role_arn   = aws_iam_role.state_machine.arn
  type       = "STANDARD"
  definition = jsonencode(local.definition)

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.state_machine.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tracing_configuration {
    enabled = true
  }

  depends_on = [aws_iam_role_policy.state_machine]
}

# Schedules ------------------------------------------------------------------

resource "aws_scheduler_schedule_group" "this" {
  name = var.name_prefix
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name_prefix}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    sid       = "StartDaily"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.daily.arn]
  }

  statement {
    sid       = "InvokeFetch"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.fetch.arn]
  }

  statement {
    sid       = "RunOpenAlexTask"
    actions   = ["ecs:RunTask"]
    resources = ["arn:${var.partition}:ecs:${var.region}:${var.account_id}:task-definition/${local.task_family}:*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.this.arn]
    }
  }

  statement {
    sid       = "PassTaskRoles"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.task.arn, aws_iam_role.task_execution.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    sid       = "DeadLetter"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.dlq.arn]
  }

  statement {
    sid       = "UseLakeKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "targets"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

resource "aws_scheduler_schedule" "daily" {
  count                        = local.direct_schedules ? 0 : 1
  name                         = local.state_machine_name
  group_name                   = aws_scheduler_schedule_group.this.name
  description                  = "Start ${local.state_machine_name} after the 23:00 UTC Retraction Watch publish"
  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = var.schedule_timezone
  kms_key_arn                  = var.kms_key_arn

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.daily.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({})

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 3
    }

    dead_letter_config {
      arn = aws_sqs_queue.dlq.arn
    }
  }
}

resource "aws_scheduler_schedule" "fetch" {
  for_each                     = local.direct_schedules ? local.lambda_sources : {}
  name                         = "${var.name_prefix}-fetch-${each.key}"
  group_name                   = aws_scheduler_schedule_group.this.name
  description                  = "Land the newest ${each.value} snapshot"
  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = var.schedule_timezone
  kms_key_arn                  = var.kms_key_arn

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.fetch.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ source = each.key })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 3
    }

    dead_letter_config {
      arn = aws_sqs_queue.dlq.arn
    }
  }
}

resource "aws_scheduler_schedule" "openalex" {
  count                        = local.direct_schedules ? 1 : 0
  name                         = "${var.name_prefix}-fetch-openalex"
  group_name                   = aws_scheduler_schedule_group.this.name
  description                  = "Run the OpenAlex fetch on Fargate"
  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = var.schedule_timezone
  kms_key_arn                  = var.kms_key_arn

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.this.arn
    role_arn = aws_iam_role.scheduler.arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.openalex.arn
      launch_type         = "FARGATE"
      platform_version    = "LATEST"
      task_count          = 1
      propagate_tags      = "TASK_DEFINITION"

      network_configuration {
        subnets          = local.subnet_ids
        security_groups  = [aws_security_group.openalex.id]
        assign_public_ip = true
      }
    }

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 3
    }

    dead_letter_config {
      arn = aws_sqs_queue.dlq.arn
    }
  }
}
