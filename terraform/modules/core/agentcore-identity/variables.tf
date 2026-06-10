variable "agent_name" {
  description = "Name of the agent to create a workload identity for"
  type        = string
}

variable "agent_runtime_id" {
  description = "ID of the AgentCore Runtime associated with this agent"
  type        = string
}

variable "project_tag" {
  description = "Project tag"
  type        = string
}

variable "environment_tag" {
  description = "Environment tag"
  type        = string
}
