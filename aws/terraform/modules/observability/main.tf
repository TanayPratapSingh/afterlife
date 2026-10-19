# Every failure mode that should wake someone, routed to one SNS topic:
#
#   Lambda Errors and Throttles     each of fetch, load_registry, api
#   DLQ depth                       an event that exhausted its retries
#   State machine failures          a source did not land, or the run timed out
#   ECS task failure                the OpenAlex container exited non zero or never started
#   API 5xx                         the lookup API is failing callers
#   Registry not refreshed          no export loaded for two days
#
# The last one matters most for a registry: a stale table answers every query
# confidently and wrongly, and nothing else would notice.

data "aws_partition" "current" {}

resource "aws_sns_topic" "alerts" {
  name              = "${var.name_prefix}-alerts"
  kms_master_key_id = var.kms_key_arn
}

data "aws_iam_policy_document" "alerts" {
  statement {
    sid = "AccountManagesTopic"
    actions = [
      "sns:GetTopicAttributes", "sns:SetTopicAttributes", "sns:AddPermission",
      "sns:RemovePermission", "sns:DeleteTopic", "sns:Subscribe",
      "sns:ListSubscriptionsByTopic", "sns:Publish",
    ]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${var.account_id}:root"]
    }
  }

  statement {
    sid       = "AlarmsAndEventsPublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com", "events.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts.json
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

locals {
  actions = [aws_sns_topic.alerts.arn]

  lambda_alarms = merge(
    { for key, fn in var.lambda_function_names : "${key}-errors" => { fn = fn, metric = "Errors" } },
    { for key, fn in var.lambda_function_names : "${key}-throttles" => { fn = fn, metric = "Throttles" } },
  )
}

resource "aws_cloudwatch_metric_alarm" "lambda" {
  for_each            = local.lambda_alarms
  alarm_name          = "${var.name_prefix}-${each.key}"
  alarm_description   = "${each.value.fn} reported ${each.value.metric} in the last 5 minutes."
  namespace           = "AWS/Lambda"
  metric_name         = each.value.metric
  dimensions          = { FunctionName = each.value.fn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  ok_actions          = local.actions
}

resource "aws_cloudwatch_metric_alarm" "dlq" {
  for_each            = var.dlq_names
  alarm_name          = "${var.name_prefix}-${each.key}-dlq-not-empty"
  alarm_description   = "An event in ${each.value} exhausted its retries. Read it, fix the cause, then redrive or delete it."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = each.value }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  ok_actions          = local.actions
}

resource "aws_cloudwatch_metric_alarm" "state_machine" {
  for_each            = toset(["ExecutionsFailed", "ExecutionsTimedOut", "ExecutionsAborted"])
  alarm_name          = "${var.name_prefix}-daily-${lower(trimprefix(each.value, "Executions"))}"
  alarm_description   = "The daily state machine reported ${each.value}. Its Fetch state output says which source."
  namespace           = "AWS/States"
  metric_name         = each.value
  dimensions          = { StateMachineArn = var.state_machine_arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${var.name_prefix}-api-5xx"
  alarm_description   = "The lookup API returned 5xx responses in two consecutive minutes."
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  dimensions          = { ApiId = var.api_id, Stage = "$default" }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  ok_actions          = local.actions
}

resource "aws_cloudwatch_metric_alarm" "registry_stale" {
  alarm_name          = "${var.name_prefix}-registry-not-refreshed"
  alarm_description   = "No registry export was loaded in two days. Every lookup is answering from a stale table."
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  dimensions          = { FunctionName = var.loader_function_name }
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  # No invocations at all produces no data points; that is the failure.
  treat_missing_data = "breaching"
  alarm_actions      = local.actions
  ok_actions         = local.actions
}

# ECS publishes task state changes to EventBridge; a stopped OpenAlex task
# that failed to start or exited non zero becomes an alert. This also covers
# the direct scheduler mode, where no state machine watches the task.
resource "aws_cloudwatch_event_rule" "openalex_failed" {
  name        = "${var.name_prefix}-openalex-task-failed"
  description = "OpenAlex fetch task stopped without a clean exit"
  event_pattern = jsonencode({
    source        = ["aws.ecs"]
    "detail-type" = ["ECS Task State Change"]
    detail = {
      clusterArn = [var.ecs_cluster_arn]
      lastStatus = ["STOPPED"]
      "$or" = [
        { stopCode = ["TaskFailedToStart"] },
        { containers = { exitCode = [{ "anything-but" = 0 }] } },
      ]
    }
  })
}

resource "aws_cloudwatch_event_target" "openalex_failed" {
  rule = aws_cloudwatch_event_rule.openalex_failed.name
  arn  = aws_sns_topic.alerts.arn
}

resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
