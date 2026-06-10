variable "project_tag" {
  description = "Project tag applied to all resources"
  type        = string
}

variable "environment_tag" {
  description = "Environment tag applied to all resources (e.g. dev, staging, prod)"
  type        = string
}

variable "latency_threshold_seconds" {
  description = "P99 agent latency threshold in seconds for CloudWatch alarm"
  type        = number
  default     = 30
}

variable "log_retention_days" {
  description = "Number of days to retain agent logs in CloudWatch Logs"
  type        = number
  default     = 30
}

variable "kms_key_arn" {
  description = "ARN of the KMS key for log group encryption (empty = AWS-managed)"
  type        = string
  default     = ""
}

variable "alarm_email" {
  description = "Email address for alarm notifications (leave empty to skip email subscription)"
  type        = string
  default     = ""
}

variable "agent_names" {
  description = "List of agent names from hierarchy.json for per-agent log groups and metric filters"
  type        = list(string)
  default     = []
}

variable "runtime_arn" {
  description = "Supervisor AgentCore Runtime ARN (for trace delivery)"
  type        = string
  default     = ""
}

variable "gateway_arn" {
  description = "AgentCore Gateway ARN (for trace and log delivery)"
  type        = string
  default     = ""
}

variable "memory_arn" {
  description = "AgentCore Memory ARN (for trace delivery)"
  type        = string
  default     = ""
}

variable "enable_runtime_tracing" {
  description = "Whether to create runtime trace delivery (set to true when runtime is deployed)"
  type        = bool
  default     = false
}

variable "enable_gateway_tracing" {
  description = "Whether to create gateway trace/log delivery (set to true when gateway is deployed)"
  type        = bool
  default     = false
}

variable "enable_memory_tracing" {
  description = "Whether to create memory trace delivery (set to true when memory is deployed)"
  type        = bool
  default     = false
}
