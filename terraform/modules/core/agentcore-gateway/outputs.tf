output "gateway_endpoint" {
  description = "Endpoint URL of the AgentCore Gateway"
  value       = aws_bedrockagentcore_gateway.this.gateway_url
}

output "gateway_id" {
  description = "ID of the AgentCore Gateway"
  value       = aws_bedrockagentcore_gateway.this.gateway_id
}

output "gateway_arn" {
  description = "ARN of the AgentCore Gateway"
  value       = aws_bedrockagentcore_gateway.this.gateway_arn
}
