variable "project_tag" {
  description = "Project tag applied to all resources"
  type        = string
}

variable "environment_tag" {
  description = "Environment tag applied to all resources (e.g. dev, staging, prod)"
  type        = string
}

variable "callback_urls" {
  description = "List of allowed callback URLs for the Cognito app client"
  type        = list(string)
  default     = ["http://localhost:8501/callback"]
}

variable "logout_urls" {
  description = "List of allowed logout URLs for the Cognito app client"
  type        = list(string)
  default     = ["http://localhost:8501"]
}

variable "generate_secret" {
  description = "Generate a client secret (confidential client). Required for Amazon Quick's user-auth OAuth, which asks for a Client Secret. The AG-UI frontend uses a public client (false)."
  type        = bool
  default     = false
}

variable "extra_oauth_scopes" {
  description = "Additional allowed OAuth scopes appended to the base [openid, email, profile]. The root module passes [\"phone\"] only when gateway_auth = oauth (Amazon Quick requests all of Cognito's advertised scopes). Empty for the AG-UI/iam path so existing deployments see no change."
  type        = list(string)
  default     = []
}
