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

variable "jwt_validation_claim" {
  description = <<-EOT
    Which JWT claim the gateway validates the Cognito client ID against:
      - "client" (default): validate the `client_id` claim via allowed_clients.
        Use for clients that send Cognito ACCESS tokens (e.g. Amazon Quick,
        and OAuth resource-access clients generally). Access tokens have no `aud`
        claim — they carry the client ID in `client_id` — so audience validation
        rejects them with 403 insufficient_scope.
      - "audience": validate the `aud` claim via allowed_audience. Use for clients
        that send Cognito ID tokens (e.g. the AG-UI frontend), whose `aud` is the
        client ID.
  EOT
  type        = string
  default     = "client"

  validation {
    condition     = contains(["client", "audience"], var.jwt_validation_claim)
    error_message = "jwt_validation_claim must be one of: client, audience"
  }
}
