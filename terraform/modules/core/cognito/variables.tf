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
