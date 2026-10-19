output "api_url" {
  description = "Invoke URL of the $default stage."
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "api_id" {
  description = "HTTP API id."
  value       = aws_apigatewayv2_api.this.id
}

output "table_name" {
  description = "Registry table name."
  value       = aws_dynamodb_table.registry.name
}

output "table_arn" {
  description = "Registry table ARN."
  value       = aws_dynamodb_table.registry.arn
}

output "loader_function_name" {
  description = "Loader Lambda name."
  value       = aws_lambda_function.loader.function_name
}

output "loader_function_arn" {
  description = "Loader Lambda ARN."
  value       = aws_lambda_function.loader.arn
}

output "api_function_name" {
  description = "API Lambda name."
  value       = aws_lambda_function.api.function_name
}

output "api_function_arn" {
  description = "API Lambda ARN."
  value       = aws_lambda_function.api.arn
}

output "dlq_name" {
  description = "Dead letter queue for failed loads."
  value       = aws_sqs_queue.dlq.name
}
