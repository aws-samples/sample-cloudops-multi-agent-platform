output "api_url" {
  description = "Frontend API Gateway URL"
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "api_id" {
  description = "Frontend API Gateway ID"
  value       = aws_apigatewayv2_api.frontend.id
}

output "api_execution_arn" {
  description = "Execution ARN of the API Gateway (for Lambda permissions)"
  value       = aws_apigatewayv2_api.frontend.execution_arn
}

output "cognito_authorizer_id" {
  description = "JWT authorizer ID — reused by sibling route-only modules"
  value       = aws_apigatewayv2_authorizer.cognito.id
}
