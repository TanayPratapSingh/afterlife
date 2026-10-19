variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
}

variable "account_id" {
  description = "AWS account id."
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

variable "loader_zip_path" {
  description = "Path to load_registry.zip from aws/build.py."
  type        = string
}

variable "api_zip_path" {
  description = "Path to api.zip from aws/build.py."
  type        = string
}

variable "item_ttl_days" {
  description = "Days after as_of that an unrefreshed item expires."
  type        = number
}

variable "throttle_burst" {
  description = "Stage burst limit."
  type        = number
}

variable "throttle_rate" {
  description = "Stage steady state rate limit, requests per second."
  type        = number
}

variable "cors_allow_origins" {
  description = "Allowed browser origins."
  type        = list(string)

  validation {
    condition     = length(var.cors_allow_origins) > 0
    error_message = "Give at least one origin, or [\"*\"]."
  }
}
