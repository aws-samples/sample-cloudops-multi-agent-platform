output "runtime_id" {
  description = "ID of the AgentCore Runtime"
  value       = aws_bedrockagentcore_agent_runtime.this.agent_runtime_id
}

output "runtime_arn" {
  description = "ARN of the AgentCore Runtime"
  value       = aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn
}

output "execution_role_arn" {
  description = "ARN of the IAM execution role used by AgentCore Runtime"
  value       = local.execution_role_arn
}

output "endpoint_name" {
  description = "Name of the AgentCore Runtime Endpoint (used as qualifier)"
  value       = aws_bedrockagentcore_agent_runtime_endpoint.this.name
}
