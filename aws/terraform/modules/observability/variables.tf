variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
}

variable "account_id" {
  description = "AWS account id."
  type        = string
}

variable "kms_key_arn" {
  description = "Customer managed key for the alerts topic."
  type        = string
}

variable "alert_email" {
  description = "Subscribed to the alerts topic and to budget notifications."
  type        = string
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget in USD."
  type        = number
}

variable "lambda_function_names" {
  description = "Functions to alarm on, keyed by a short stable name."
  type        = map(string)
}

variable "dlq_names" {
  description = "Dead letter queues to alarm on, keyed by a short stable name."
  type        = map(string)
}

variable "state_machine_arn" {
  description = "The daily state machine."
  type        = string
}

variable "api_id" {
  description = "HTTP API id."
  type        = string
}

variable "ecs_cluster_arn" {
  description = "Cluster running the OpenAlex task."
  type        = string
}

variable "loader_function_name" {
  description = "The registry loader, whose invocations prove the table is being refreshed."
  type        = string
}
