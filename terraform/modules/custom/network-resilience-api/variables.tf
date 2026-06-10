variable "project_tag" {
  type = string
}

variable "environment_tag" {
  type = string
}

variable "lambda_zip_path" {
  description = "Path to the network-resilience-api Lambda zip file"
  type        = string
}

variable "api_gateway_id" {
  description = "API Gateway HTTP API ID (exported by the frontend-api module). This module attaches routes to the existing gateway — it does NOT create a new one."
  type        = string
}

variable "api_gateway_execution_arn" {
  description = "API Gateway execution ARN — used for the Lambda invoke permission."
  type        = string
}

variable "cognito_authorizer_id" {
  description = "JWT authorizer ID (exported by the frontend-api module). Reused so browser callers use the same Cognito session token for both APIs."
  type        = string
}

variable "cross_account_role_arns" {
  description = "Optional list of spoke-account roles to assume for Phase 7 cross-account enrichment. Empty by default; permission to AssumeRole is granted only for ARNs in this list."
  type        = list(string)
  default     = []
}
