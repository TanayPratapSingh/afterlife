output "topic_arn" {
  description = "SNS topic every alarm notifies."
  value       = aws_sns_topic.alerts.arn
}

output "alarm_names" {
  description = "Every CloudWatch alarm this module creates."
  value = concat(
    [for a in aws_cloudwatch_metric_alarm.lambda : a.alarm_name],
    [for a in aws_cloudwatch_metric_alarm.dlq : a.alarm_name],
    [for a in aws_cloudwatch_metric_alarm.state_machine : a.alarm_name],
    [aws_cloudwatch_metric_alarm.api_5xx.alarm_name, aws_cloudwatch_metric_alarm.registry_stale.alarm_name],
  )
}
