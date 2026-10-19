output "api_url" {
  description = "Base URL of the lookup API. Try <api_url>/v1/lookup/10.1000/xyz."
  value       = module.serving.api_url
}

output "bucket" {
  description = "The lake bucket: landing/, export/, athena-results/."
  value       = module.landing.bucket_id
}

output "table" {
  description = "DynamoDB table serving the registry."
  value       = module.serving.table_name
}

output "kms_key_arn" {
  description = "The project's customer managed key."
  value       = aws_kms_key.lake.arn
}

output "fetch_function_name" {
  description = "Lambda that lands Retraction Watch and PubMed."
  value       = module.ingest.fetch_function_name
}

output "state_machine_arn" {
  description = "The afterlife-daily state machine."
  value       = module.ingest.state_machine_arn
}

output "ecr_repository_url" {
  description = "Push the OpenAlex fetcher image here, tagged with openalex_image_tag."
  value       = module.ingest.ecr_repository_url
}

output "openalex_secret_arn" {
  description = "Secrets Manager secret for the OpenAlex API key, when enabled. Its value is set outside Terraform."
  value       = module.ingest.openalex_secret_arn
}

output "athena_workgroup" {
  description = "Athena workgroup with the scan cutoff enforced."
  value       = module.analytics.workgroup_name
}

output "github_deploy_role_arn" {
  description = "Role the main branch's GitHub Actions workflows assume."
  value       = module.ci.deploy_role_arn
}

output "alerts_topic_arn" {
  description = "SNS topic every alarm notifies."
  value       = module.observability.topic_arn
}
