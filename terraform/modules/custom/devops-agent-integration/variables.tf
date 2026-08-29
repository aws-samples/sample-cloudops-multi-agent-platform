variable "project_tag" {
  type = string
}

variable "environment_tag" {
  type = string
}

variable "workflow_zip_path" {
  type = string
}

variable "artifact_bucket" {
  type = string
}

variable "health_table_stream_arn" {
  type    = string
  default = ""
}

variable "agent_space_id" {
  type = string
}

variable "agent_space_region" {
  type = string
}

variable "integration_enabled" {
  type = bool
}

variable "automatic_health_enabled" {
  type = bool
}

variable "max_concurrency" {
  type = number
}

variable "automatic_daily_budget" {
  type = number
}

variable "max_age_minutes" {
  type = number
}

variable "sweep_interval_minutes" {
  type = number
}

variable "coverage_cache_ttl_seconds" {
  type = number
}

variable "log_retention_days" {
  type = number
}

variable "kms_key_arn" {
  type    = string
  default = ""
}
