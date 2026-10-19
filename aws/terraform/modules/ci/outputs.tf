output "deploy_role_arn" {
  description = "Role for aws-actions/configure-aws-credentials in the main branch workflow."
  value       = aws_iam_role.deploy.arn
}

output "oidc_provider_arn" {
  description = "The GitHub OIDC provider the role trusts."
  value       = local.provider_arn
}
