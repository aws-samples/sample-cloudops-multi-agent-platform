output "lambda_arn" {
  description = "ARN of the network-resilience-api Lambda"
  value       = aws_lambda_function.api.arn
}

output "lambda_name" {
  description = "Name of the network-resilience-api Lambda"
  value       = aws_lambda_function.api.function_name
}
