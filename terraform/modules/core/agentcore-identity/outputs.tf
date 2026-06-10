output "workload_identity_id" {
  description = "ID of the workload identity"
  value       = aws_bedrockagentcore_workload_identity.this.id
}

output "workload_identity_arn" {
  description = "ARN of the workload identity"
  value       = aws_bedrockagentcore_workload_identity.this.arn
}
