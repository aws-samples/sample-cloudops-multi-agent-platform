output "guardrail_id" {
  description = "Bedrock Guardrail ID"
  value       = aws_bedrock_guardrail.platform.guardrail_id
}

output "guardrail_version" {
  description = "Bedrock Guardrail version number"
  value       = aws_bedrock_guardrail_version.v1.version
}

output "guardrail_arn" {
  description = "Bedrock Guardrail ARN"
  value       = aws_bedrock_guardrail.platform.guardrail_arn
}
