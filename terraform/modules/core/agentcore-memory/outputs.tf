output "memory_id" {
  description = "AgentCore Memory resource ID for use by agents"
  value       = aws_bedrockagentcore_memory.this.id
}

output "memory_arn" {
  description = "AgentCore Memory resource ARN"
  value       = aws_bedrockagentcore_memory.this.arn
}
