variable "project_tag" {
  description = "Project tag applied to all resources"
  type        = string
}

variable "environment_tag" {
  description = "Environment tag applied to all resources"
  type        = string
}

variable "lambda_tool_arns" {
  description = "Map of Lambda tool name to Lambda function ARN for gateway target registration"
  type        = map(string)
  default     = {}
}

variable "enable_semantic_search" {
  description = "Enable semantic search for tool discovery on the gateway"
  type        = bool
  default     = true
}

variable "gateway_auth" {
  description = "Gateway auth type: iam (default, for agent-to-gateway) or oauth (Cognito JWT, for external clients)"
  type        = string
  default     = "iam"
}

variable "cognito_user_pool_id" {
  description = "Cognito User Pool ID (required when gateway_auth = oauth)"
  type        = string
  default     = ""
}

variable "cognito_app_client_id" {
  description = "Cognito App Client ID (required when gateway_auth = oauth)"
  type        = string
  default     = ""
}
