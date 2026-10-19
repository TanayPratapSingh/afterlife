output "workgroup_name" {
  description = "Athena workgroup."
  value       = aws_athena_workgroup.this.name
}

output "database_name" {
  description = "Glue catalog database."
  value       = aws_glue_catalog_database.this.name
}

output "table_name" {
  description = "Glue table over the registry parquet."
  value       = aws_glue_catalog_table.registry.name
}
