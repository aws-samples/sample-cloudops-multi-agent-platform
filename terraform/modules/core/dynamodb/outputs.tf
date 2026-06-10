output "report_prompts_table_name" {
  description = "Name of the report prompts DynamoDB table"
  value       = aws_dynamodb_table.report_prompts.name
}

output "report_prompts_table_arn" {
  description = "ARN of the report prompts DynamoDB table"
  value       = aws_dynamodb_table.report_prompts.arn
}

output "agent_registry_table_name" {
  description = "Name of the agent registry DynamoDB table"
  value       = aws_dynamodb_table.agent_registry.name
}

output "agent_registry_table_arn" {
  description = "ARN of the agent registry DynamoDB table"
  value       = aws_dynamodb_table.agent_registry.arn
}

output "report_templates_table_name" {
  description = "Name of the report templates DynamoDB table"
  value       = aws_dynamodb_table.report_templates.name
}

output "report_templates_table_arn" {
  description = "ARN of the report templates DynamoDB table"
  value       = aws_dynamodb_table.report_templates.arn
}
