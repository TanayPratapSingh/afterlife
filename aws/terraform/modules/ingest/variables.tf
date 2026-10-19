variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
}

variable "account_id" {
  description = "AWS account id."
  type        = string
}

variable "partition" {
  description = "AWS partition (aws, aws-cn, aws-us-gov)."
  type        = string
}

variable "region" {
  description = "AWS region."
  type        = string
}

variable "bucket_id" {
  description = "Lake bucket name."
  type        = string
}

variable "bucket_arn" {
  description = "Lake bucket ARN."
  type        = string
}

variable "kms_key_arn" {
  description = "Customer managed key."
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention."
  type        = number
}

variable "fetch_zip_path" {
  description = "Path to fetch.zip from aws/build.py."
  type        = string
}

variable "orchestrator" {
  description = "step_functions or scheduler."
  type        = string
}

variable "schedule_expression" {
  description = "EventBridge Scheduler expression for the daily run."
  type        = string
}

variable "schedule_timezone" {
  description = "Time zone for the schedule expression."
  type        = string
}

variable "openalex_budget_usd" {
  description = "USD cap per OpenAlex run."
  type        = number
}

variable "openalex_api_key_enabled" {
  description = "Create and inject an OpenAlex API key secret."
  type        = bool
}

variable "openalex_image_tag" {
  description = "ECR image tag for the OpenAlex task."
  type        = string
}

variable "fargate_cpu" {
  description = "Fargate CPU units."
  type        = number
}

variable "fargate_memory" {
  description = "Fargate memory in MiB."
  type        = number
}

variable "vpc_id" {
  description = "VPC for the task; empty for the default VPC."
  type        = string
}

variable "subnet_ids" {
  description = "Subnets for the task; empty for the default subnets."
  type        = list(string)
}

variable "databricks_host" {
  description = "Databricks workspace host; empty disables the step."
  type        = string
}

variable "databricks_job_id" {
  description = "Databricks job id to run."
  type        = number
}

variable "databricks_token" {
  description = "Databricks token for the EventBridge connection."
  type        = string
  sensitive   = true
}
