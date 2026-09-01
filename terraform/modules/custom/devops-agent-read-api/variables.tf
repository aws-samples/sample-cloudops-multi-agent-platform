variable "project_tag" {
  type = string
}

variable "environment_tag" {
  type = string
}

variable "lambda_zip_path" {
  type = string
}

variable "api_gateway_id" {
  type = string
}

variable "api_gateway_execution_arn" {
  type = string
}

variable "cognito_authorizer_id" {
  type = string
}

variable "investigations_table_name" {
  type = string
}

variable "investigations_table_arn" {
  type = string
}

variable "agent_space_id" {
  type = string
}

variable "reconcile_function_arn" {
  type = string
}

variable "kms_key_arn" {
  type    = string
  default = ""
}

variable "log_retention_days" {
  type = number
}
