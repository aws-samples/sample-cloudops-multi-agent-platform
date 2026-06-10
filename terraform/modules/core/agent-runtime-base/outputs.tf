output "runtime_id" {
  description = "ID of the AgentCore Runtime"
  value       = var.enabled ? aws_bedrockagentcore_agent_runtime.this[0].agent_runtime_id : ""
}

output "runtime_arn" {
  description = "ARN of the AgentCore Runtime"
  value       = var.enabled ? aws_bedrockagentcore_agent_runtime.this[0].agent_runtime_arn : ""
}

output "endpoint_name" {
  description = "Name of the AgentCore Runtime Endpoint (used as qualifier)"
  value       = var.enabled ? aws_bedrockagentcore_agent_runtime_endpoint.this[0].name : ""
}

output "execution_role_arn" {
  description = "ARN of the IAM execution role for this agent's runtime"
  value       = var.enabled ? aws_iam_role.agent_execution[0].arn : ""
}

output "a2a_endpoint" {
  description = "Full A2A invocation endpoint URL for this agent"
  value       = local.a2a_endpoint
}
