# All outputs use try() to handle conditional modules gracefully

output "cloudfront_url" {
  description = "CloudFront distribution URL for the frontend"
  value       = try(module.frontend[0].cloudfront_url, "")
}

output "frontend_bucket" {
  description = "S3 bucket name for frontend assets"
  value       = try(module.frontend[0].s3_bucket_name, "")
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID (for cache invalidation)"
  value       = try(module.frontend[0].cloudfront_distribution_id, "")
}

output "gateway_endpoint" {
  description = "AgentCore Gateway endpoint URL"
  value       = try(module.agentcore_gateway[0].gateway_endpoint, "")
}

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID"
  value       = try(module.cognito[0].user_pool_id, "")
}

output "cognito_app_client_id" {
  description = "Cognito App Client ID"
  value       = try(module.cognito[0].app_client_id, "")
}

output "cognito_domain" {
  description = "Cognito hosted UI domain"
  value       = try(module.cognito[0].cognito_domain, "")
}

output "agentcore_runtime_id" {
  description = "Supervisor AgentCore Runtime ID"
  value       = try(module.agentcore_runtime[0].runtime_id, "")
}

output "supervisor_url" {
  description = "Supervisor Agent invoke endpoint (HTTPS with Bearer token)"
  value       = var.deploy_agents ? "https://bedrock-agentcore.${var.aws_region}.amazonaws.com/runtimes/${urlencode(module.agentcore_runtime[0].runtime_arn)}/invocations?qualifier=${module.agentcore_runtime[0].endpoint_name}" : ""
}

output "endpoint_name" {
  description = "Supervisor AgentCore Runtime Endpoint name (used as qualifier)"
  value       = try(module.agentcore_runtime[0].endpoint_name, "")
}

output "agentcore_memory_id" {
  description = "AgentCore Memory resource ID"
  value       = try(module.agentcore_memory[0].memory_id, "")
}

output "agentcore_memory_arn" {
  description = "AgentCore Memory ARN"
  value       = try(module.agentcore_memory[0].memory_arn, "")
}

output "agent_registry_table_name" {
  description = "Agent registry DynamoDB table name"
  value       = try(module.dynamodb[0].agent_registry_table_name, "")
}

output "agent_runtime_ids" {
  description = "Map of agent name to AgentCore Runtime ID"
  value       = { for k, v in module.agents : k => v.runtime_id }
}

output "agent_endpoint_names" {
  description = "Map of agent name to AgentCore Runtime Endpoint name"
  value       = { for k, v in module.agents : k => v.endpoint_name }
}

output "lambda_tool_arns" {
  description = "Map of tool name to Lambda function ARN"
  value       = { for k, v in module.lambda_tools : k => v.lambda_function_arn }
}

output "supervisor_runtime_arn" {
  description = "Supervisor AgentCore Runtime ARN"
  value       = try(module.agentcore_runtime[0].runtime_arn, "")
}

output "gateway_arn" {
  description = "AgentCore Gateway ARN"
  value       = try(module.agentcore_gateway[0].gateway_arn, "")
}

output "gateway_id" {
  description = "AgentCore Gateway ID"
  value       = try(module.agentcore_gateway[0].gateway_id, "")
}

output "frontend_api_url" {
  description = "Frontend API Gateway URL for session/template/report CRUD"
  value       = try(module.frontend_api[0].api_url, "")
}
