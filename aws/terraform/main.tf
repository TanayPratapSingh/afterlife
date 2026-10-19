provider "aws" {
  region = var.region

  default_tags {
    tags = merge(
      {
        Project     = "afterlife"
        Environment = var.environment
        ManagedBy   = "terraform"
        Repository  = "github.com/${var.github_repository}"
      },
      var.extra_tags,
    )
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = data.aws_region.current.name

  # Built by `python aws/build.py`; Terraform hashes them, so build first.
  build_dir = "${path.root}/../build"
}

module "landing" {
  source = "./modules/landing"

  name_prefix             = var.name_prefix
  account_id              = local.account_id
  kms_key_arn             = aws_kms_key.lake.arn
  glacier_ir_days         = var.landing_glacier_ir_days
  noncurrent_version_days = var.noncurrent_version_days
}

module "ingest" {
  source = "./modules/ingest"

  name_prefix        = var.name_prefix
  account_id         = local.account_id
  partition          = local.partition
  region             = local.region
  bucket_id          = module.landing.bucket_id
  bucket_arn         = module.landing.bucket_arn
  kms_key_arn        = aws_kms_key.lake.arn
  log_retention_days = var.log_retention_days
  fetch_zip_path     = "${local.build_dir}/fetch.zip"

  orchestrator        = var.orchestrator
  schedule_expression = var.schedule_expression
  schedule_timezone   = var.schedule_timezone

  openalex_budget_usd      = var.openalex_budget_usd
  openalex_api_key_enabled = var.openalex_api_key_enabled
  openalex_image_tag       = var.openalex_image_tag
  fargate_cpu              = var.fargate_cpu
  fargate_memory           = var.fargate_memory
  vpc_id                   = var.vpc_id
  subnet_ids               = var.subnet_ids

  databricks_host   = var.databricks_host
  databricks_job_id = var.databricks_job_id
  databricks_token  = var.databricks_token
}

module "serving" {
  source = "./modules/serving"

  name_prefix        = var.name_prefix
  account_id         = local.account_id
  bucket_id          = module.landing.bucket_id
  bucket_arn         = module.landing.bucket_arn
  kms_key_arn        = aws_kms_key.lake.arn
  log_retention_days = var.log_retention_days
  loader_zip_path    = "${local.build_dir}/load_registry.zip"
  api_zip_path       = "${local.build_dir}/api.zip"
  item_ttl_days      = var.registry_item_ttl_days
  throttle_burst     = var.api_throttle_burst
  throttle_rate      = var.api_throttle_rate
  cors_allow_origins = var.cors_allow_origins
}

module "analytics" {
  source = "./modules/analytics"

  name_prefix          = var.name_prefix
  account_id           = local.account_id
  bucket_id            = module.landing.bucket_id
  kms_key_arn          = aws_kms_key.lake.arn
  bytes_scanned_cutoff = var.athena_bytes_scanned_cutoff
  projection_start     = var.registry_projection_start
}

module "ci" {
  source = "./modules/ci"

  name_prefix          = var.name_prefix
  github_repository    = var.github_repository
  github_branch        = var.github_branch
  create_oidc_provider = var.create_github_oidc_provider
  partition            = local.partition
  account_id           = local.account_id
  lambda_function_arns = [module.ingest.fetch_function_arn, module.serving.loader_function_arn, module.serving.api_function_arn]
  ecr_repository_arn   = module.ingest.ecr_repository_arn
}

module "observability" {
  source = "./modules/observability"

  name_prefix        = var.name_prefix
  account_id         = local.account_id
  kms_key_arn        = aws_kms_key.lake.arn
  alert_email        = var.alert_email
  monthly_budget_usd = var.monthly_budget_usd

  lambda_function_names = {
    fetch         = module.ingest.fetch_function_name
    load_registry = module.serving.loader_function_name
    api           = module.serving.api_function_name
  }
  dlq_names = {
    ingest  = module.ingest.dlq_name
    serving = module.serving.dlq_name
  }
  state_machine_arn    = module.ingest.state_machine_arn
  api_id               = module.serving.api_id
  ecs_cluster_arn      = module.ingest.ecs_cluster_arn
  loader_function_name = module.serving.loader_function_name
}
