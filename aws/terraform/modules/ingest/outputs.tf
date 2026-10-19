output "fetch_function_name" {
  description = "Fetch Lambda name."
  value       = aws_lambda_function.fetch.function_name
}

output "fetch_function_arn" {
  description = "Fetch Lambda ARN."
  value       = aws_lambda_function.fetch.arn
}

output "dlq_name" {
  description = "Dead letter queue for failed scheduled fetches."
  value       = aws_sqs_queue.dlq.name
}

output "state_machine_arn" {
  description = "The afterlife-daily state machine."
  value       = aws_sfn_state_machine.daily.arn
}

output "ecs_cluster_arn" {
  description = "Cluster the OpenAlex task runs in."
  value       = aws_ecs_cluster.this.arn
}

output "task_definition_arn" {
  description = "Current OpenAlex task definition revision."
  value       = aws_ecs_task_definition.openalex.arn
}

output "ecr_repository_url" {
  description = "ECR repository for the OpenAlex image."
  value       = aws_ecr_repository.openalex.repository_url
}

output "ecr_repository_arn" {
  description = "ECR repository ARN."
  value       = aws_ecr_repository.openalex.arn
}

output "openalex_secret_arn" {
  description = "OpenAlex API key secret, or empty when disabled."
  value       = local.openalex_secret_arn
}

output "state_machine_definition" {
  description = "The rendered Amazon States Language definition, for tests and review."
  value       = aws_sfn_state_machine.daily.definition
}

output "schedule_names" {
  description = "EventBridge Scheduler schedules created for the chosen orchestrator."
  value = concat(
    [for s in aws_scheduler_schedule.daily : s.name],
    [for s in aws_scheduler_schedule.fetch : s.name],
    [for s in aws_scheduler_schedule.openalex : s.name],
  )
}
