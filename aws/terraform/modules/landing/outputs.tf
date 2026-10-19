output "bucket_id" {
  description = "Lake bucket name."
  value       = aws_s3_bucket.lake.id
}

output "bucket_arn" {
  description = "Lake bucket ARN."
  value       = aws_s3_bucket.lake.arn
}

output "log_bucket_id" {
  description = "Bucket receiving S3 server access logs for the lake."
  value       = aws_s3_bucket.logs.id
}

output "prefixes" {
  description = "The lake's prefixes and what lives under each."
  value = {
    landing        = "landing/"
    export         = "export/"
    athena_results = "athena-results/"
  }
}
