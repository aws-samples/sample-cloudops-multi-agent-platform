output "alerts_topic_arn" {
  description = "SNS topic ARN for alarm notifications"
  value       = aws_sns_topic.alerts.arn
}

output "runtime_logs_log_group_name" {
  description = "CloudWatch log group name for vended runtime application logs (only populated when enable_runtime_tracing is true)"
  value       = var.enable_runtime_tracing ? aws_cloudwatch_log_group.runtime_logs[0].name : ""
}
