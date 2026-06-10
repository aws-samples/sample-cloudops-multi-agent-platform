variable "enabled" {
  description = "Whether to create resources for this agent"
  type        = bool
  default     = true
}

variable "container_image" {
  description = "ECR image URI for the agent container"
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

variable "agent_name" {
  description = "Agent name with hyphens (e.g., finops-agent)"
  type        = string
}

variable "agent_description" {
  description = "Human-readable description of the agent's capabilities"
  type        = string
  default     = ""
}

variable "parent_agent" {
  description = "Parent agent name (empty for supervisor's direct children listed under supervisor)"
  type        = string
  default     = ""
}

variable "agent_registry_table_name" {
  description = "Name of the DynamoDB agent registry table"
  type        = string
}

variable "agent_registry_table_arn" {
  description = "ARN of the DynamoDB agent registry table (for IAM permissions)"
  type        = string
  default     = ""
}

variable "agentcore_gateway_endpoint" {
  description = "AgentCore Gateway endpoint URL"
  type        = string
  default     = ""
}

variable "agentcore_gateway_arn" {
  description = "AgentCore Gateway ARN (for IAM InvokeGateway permission on leaf agents)"
  type        = string
  default     = ""
}

variable "agentcore_memory_id" {
  description = "AgentCore Memory resource ID (empty string disables memory)"
  type        = string
  default     = ""
}

variable "is_mid_level" {
  description = "Whether this agent is a mid-level orchestrating agent (affects memory permissions)"
  type        = bool
  default     = false
}

variable "protocol" {
  description = "Agent protocol: A2A or HTTP"
  type        = string
  default     = "A2A"
}

variable "bedrock_model_id" {
  description = "Default Bedrock model ID injected as BEDROCK_MODEL_ID env var. Empty string keeps the agent_base.py hardcoded fallback."
  type        = string
  default     = ""
}



variable "kms_key_arn" {
  description = "ARN of the KMS key used to encrypt DynamoDB tables the agent reads (registry). Grants kms:Decrypt so registry scans succeed. Empty = no KMS statement (AWS-managed encryption)."
  type        = string
  default     = ""
}
