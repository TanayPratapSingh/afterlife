variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
}

variable "account_id" {
  description = "AWS account id, used to make bucket names globally unique."
  type        = string
}

variable "kms_key_arn" {
  description = "Customer managed key for default bucket encryption."
  type        = string
}

variable "glacier_ir_days" {
  description = "Days before landed snapshots move to Glacier Instant Retrieval."
  type        = number
}

variable "noncurrent_version_days" {
  description = "Days a noncurrent object version is kept."
  type        = number
}
