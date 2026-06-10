variable "project_tag" {
  description = "Project tag"
  type        = string
}

variable "environment_tag" {
  description = "Environment tag"
  type        = string
}

variable "event_expiry_duration" {
  description = "Number of days after which memory events expire (3-365)"
  type        = number
  default     = 90
}

variable "enable_ltm_strategies" {
  description = "Whether to create LTM strategies (summarization + user preference)"
  type        = bool
  default     = true
}
