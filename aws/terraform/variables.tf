variable "region" {
  description = "AWS region for every resource."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name, used in tags."
  type        = string
  default     = "dev"
}

variable "name_prefix" {
  description = "Prefix for resource names. The state machine is named <prefix>-daily."
  type        = string
  default     = "afterlife"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.name_prefix))
    error_message = "Use 2 to 21 lowercase letters, digits, or hyphens, starting with a letter."
  }
}

variable "extra_tags" {
  description = "Tags added to every resource on top of the defaults."
  type        = map(string)
  default     = {}
}

# Alerts and cost ----------------------------------------------------------

variable "alert_email" {
  description = "Receives alarm notifications and budget alerts. SNS sends a confirmation email first."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be an email address."
  }
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget for the account. Alerts at 80% actual and 100% forecast."
  type        = number
  default     = 10
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for every log group."
  type        = number
  default     = 365
}

# Landing zone -------------------------------------------------------------

variable "landing_glacier_ir_days" {
  description = "Days before landed snapshots move to S3 Glacier Instant Retrieval."
  type        = number
  default     = 90
}

variable "noncurrent_version_days" {
  description = "Days a deleted or replaced object version is kept before it expires."
  type        = number
  default     = 30
}

# Ingest -------------------------------------------------------------------

variable "orchestrator" {
  description = "step_functions: one daily schedule starts the afterlife-daily state machine. scheduler: one schedule per source targets Lambda and ECS directly, with no Databricks trigger."
  type        = string
  default     = "step_functions"

  validation {
    condition     = contains(["step_functions", "scheduler"], var.orchestrator)
    error_message = "orchestrator must be step_functions or scheduler."
  }
}

variable "schedule_expression" {
  description = "When the daily fetch runs. Retraction Watch publishes at about 23:00 UTC."
  type        = string
  default     = "cron(30 23 * * ? *)"
}

variable "schedule_timezone" {
  description = "Time zone for schedule_expression."
  type        = string
  default     = "UTC"
}

variable "openalex_budget_usd" {
  description = "USD cap per OpenAlex run. The keyless fetch of all retracted works cost $0.0709."
  type        = number
  default     = 0.09
}

variable "openalex_api_key_enabled" {
  description = "Create a Secrets Manager secret for an OpenAlex API key and inject it into the fetchers. Put the value with the AWS CLI before the next run."
  type        = bool
  default     = false
}

variable "openalex_image_tag" {
  description = "Tag of the OpenAlex fetcher image in ECR. Tags are immutable, so a new build needs a new tag."
  type        = string
  default     = "0.1.0"
}

variable "fargate_cpu" {
  description = "Fargate task CPU units for the OpenAlex fetch."
  type        = number
  default     = 512
}

variable "fargate_memory" {
  description = "Fargate task memory (MiB) for the OpenAlex fetch."
  type        = number
  default     = 1024
}

variable "vpc_id" {
  description = "VPC for the Fargate task. Empty means the default VPC."
  type        = string
  default     = ""
}

variable "subnet_ids" {
  description = "Public subnets for the Fargate task. Empty means the default VPC's default subnets."
  type        = list(string)
  default     = []
}

variable "databricks_host" {
  description = "Databricks workspace host, for example dbc-1234abcd-5678.cloud.databricks.com. Empty disables the Databricks step."
  type        = string
  default     = ""

  validation {
    condition     = var.databricks_host == "" || can(regex("^[a-z0-9.-]+$", var.databricks_host))
    error_message = "Give the bare host name, without https:// or a path."
  }
}

variable "databricks_job_id" {
  description = "Databricks job to run after all fetches land."
  type        = number
  default     = 0
}

variable "databricks_token" {
  description = "Databricks token for the EventBridge connection. EventBridge stores it in Secrets Manager; it also lands in Terraform state, so keep state encrypted."
  type        = string
  default     = ""
  sensitive   = true
}

# Serving ------------------------------------------------------------------

variable "registry_item_ttl_days" {
  description = "Days after its export date that a registry item expires if no newer export refreshes it."
  type        = number
  default     = 7
}

variable "api_throttle_burst" {
  description = "HTTP API burst limit (requests)."
  type        = number
  default     = 50
}

variable "api_throttle_rate" {
  description = "HTTP API steady state rate limit (requests per second)."
  type        = number
  default     = 25
}

variable "cors_allow_origins" {
  description = "Origins allowed to call the API from a browser."
  type        = list(string)
  default     = ["*"]
}

# Analytics ----------------------------------------------------------------

variable "athena_bytes_scanned_cutoff" {
  description = "Athena cancels any query in the workgroup that would scan more than this many bytes."
  type        = number
  default     = 1073741824 # 1 GiB

  validation {
    condition     = var.athena_bytes_scanned_cutoff >= 10485760
    error_message = "Athena's minimum cutoff is 10 MB (10485760 bytes)."
  }
}

variable "registry_projection_start" {
  description = "First as_of date the Athena partition projection considers."
  type        = string
  default     = "2026-01-01"
}

# CI -----------------------------------------------------------------------

variable "github_repository" {
  description = "owner/name of the repository whose main branch may deploy."
  type        = string
  default     = "TanayPratapSingh/afterlife"
}

variable "github_branch" {
  description = "The only branch whose workflows may assume the deploy role."
  type        = string
  default     = "main"
}

variable "create_github_oidc_provider" {
  description = "An account holds one GitHub OIDC provider. Set false if it already exists."
  type        = bool
  default     = true
}
