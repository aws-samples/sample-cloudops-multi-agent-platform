output "table_name" {
  value = aws_dynamodb_table.investigations.name
}

output "table_arn" {
  value = aws_dynamodb_table.investigations.arn
}

output "agent_space_arn" {
  value = local.agent_space_arn
}

output "stream_function_arn" {
  value = try(aws_lambda_function.workflow["stream"].arn, "")
}

output "event_function_arn" {
  value = aws_lambda_function.workflow["event"].arn
}

output "reconcile_function_arn" {
  value = aws_lambda_function.workflow["reconcile"].arn
}
