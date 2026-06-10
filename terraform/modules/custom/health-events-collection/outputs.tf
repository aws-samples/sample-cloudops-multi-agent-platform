output "table_name" {
  value = aws_dynamodb_table.health_events.name
}

output "table_arn" {
  value = aws_dynamodb_table.health_events.arn
}

output "queue_arn" {
  value = aws_sqs_queue.health_events.arn
}

output "collector_function_name" {
  value = aws_lambda_function.collector.function_name
}
