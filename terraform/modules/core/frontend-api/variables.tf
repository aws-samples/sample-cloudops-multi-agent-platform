variable "project_tag" {
  type = string
}

variable "environment_tag" {
  type = string
}

variable "lambda_zip_path" {
  description = "Path to the frontend Lambda zip file"
  type        = string
}

variable "cognito_user_pool_id" {
  description = "Cognito User Pool ID for JWT authorizer"
  type        = string
}

variable "cognito_app_client_id" {
  description = "Cognito App Client ID for JWT audience"
  type        = string
}

variable "agentcore_memory_id" {
  description = "AgentCore Memory ID for session operations"
  type        = string
  default     = ""
}

variable "report_table_name" {
  description = "DynamoDB table name for templates and reports"
  type        = string
  default     = ""
}

variable "report_table_arn" {
  description = "DynamoDB table ARN for IAM permissions"
  type        = string
  default     = ""
}

variable "allowed_origins" {
  description = "CORS allowed origins"
  type        = list(string)
  default     = ["*"]
}

variable "log_retention_days" {
  description = "CloudWatch log group retention in days"
  type        = number
  default     = 30
}

variable "kms_key_arn" {
  description = "ARN of the KMS key for log group encryption (empty = AWS-managed)"
  type        = string
  default     = ""
}
