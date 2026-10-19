variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
}

variable "github_repository" {
  description = "owner/name of the trusted repository."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must look like owner/name."
  }
}

variable "github_branch" {
  description = "The only trusted branch."
  type        = string
}

variable "create_oidc_provider" {
  description = "Create the account's GitHub OIDC provider, or reuse an existing one."
  type        = bool
}

variable "partition" {
  description = "AWS partition."
  type        = string
}

variable "account_id" {
  description = "AWS account id."
  type        = string
}

variable "lambda_function_arns" {
  description = "Functions whose code the deploy role may update."
  type        = list(string)
}

variable "ecr_repository_arn" {
  description = "Repository the deploy role may push to."
  type        = string
}
