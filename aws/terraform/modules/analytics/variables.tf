variable "name_prefix" {
  description = "Prefix for resource names; hyphens become underscores in the Glue database name."
  type        = string
}

variable "account_id" {
  description = "AWS account id, the expected owner of the results bucket."
  type        = string
}

variable "bucket_id" {
  description = "Lake bucket name."
  type        = string
}

variable "kms_key_arn" {
  description = "Customer managed key for query results."
  type        = string
}

variable "bytes_scanned_cutoff" {
  description = "Per query scan limit in bytes."
  type        = number
}

variable "projection_start" {
  description = "First as_of date in the partition projection range (yyyy-MM-dd)."
  type        = string
}
