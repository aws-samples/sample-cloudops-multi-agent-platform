variable "tool_name" {
  description = "Name of the Lambda tool (e.g., cost-explorer)"
  type        = string
}

variable "project_tag" {
  description = "Project tag applied to all resources"
  type        = string
}

variable "environment_tag" {
  description = "Environment tag (e.g. dev, staging, prod)"
  type        = string
}

variable "lambda_zip_path" {
  description = "Path to the Lambda deployment package zip"
  type        = string
}

variable "handler" {
  description = "Lambda handler (e.g., handler.handler)"
  type        = string
  default     = "handler.handler"
}

variable "runtime" {
  description = "Lambda runtime"
  type        = string
  default     = "python3.12"
}

variable "timeout" {
  description = "Lambda timeout in seconds"
  type        = number
  default     = 30
}

variable "memory_size" {
  description = "Lambda memory in MB"
  type        = number
  default     = 128
}

variable "iam_actions" {
  description = "List of IAM actions the Lambda needs"
  type        = list(string)
}

variable "iam_resources" {
  description = "IAM resource ARNs for the tool-specific actions (defaults to wildcard)"
  type        = list(string)
  default     = ["*"]
}

variable "env_vars" {
  description = "Environment variables for the Lambda function"
  type        = map(string)
  default     = {}
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
