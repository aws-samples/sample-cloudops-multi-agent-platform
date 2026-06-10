variable "project_tag" {
  description = "Project tag applied to all resources"
  type        = string
}

variable "environment_tag" {
  description = "Environment tag applied to all resources (e.g. dev, staging, prod)"
  type        = string
}

variable "report_prompts_table_name" {
  description = "Name of the DynamoDB table for report prompts"
  type        = string
  default     = "cloudops-report-prompts"
}

variable "agent_registry_table_name" {
  description = "Name of the DynamoDB table for agent registry"
  type        = string
  default     = "cloudops-agent-registry"
}

variable "report_templates_table_name" {
  description = "Name of the DynamoDB table for report templates and reports"
  type        = string
  default     = "report-templates"
}

variable "kms_key_arn" {
  description = "ARN of the KMS key for server-side encryption (empty = AWS-managed)"
  type        = string
  default     = ""
}
