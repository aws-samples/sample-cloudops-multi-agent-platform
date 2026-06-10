variable "project_tag" {
  type = string
}

variable "environment_tag" {
  type = string
}

variable "collector_zip_path" {
  description = "Path to the collector Lambda zip file"
  type        = string
  default     = ""
}

variable "enrichment_model_id" {
  description = <<-EOT
    Bedrock model ID for narrative enrichment (impactSummary / remediationHint /
    affectedResourceTypes). Default is Claude Haiku 4.5 via the global
    cross-region inference profile. Set to empty string to disable LLM
    enrichment entirely — the collector still writes rules-based fields
    (riskLevel, accountName) and deterministic event metadata.
  EOT
  type        = string
  default     = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "cross_account_role_arn" {
  description = <<-EOT
    Optional IAM role ARN the collector will assume before calling
    Organizations (`DescribeAccount` for account-name resolution) and
    AWS Health org-view APIs (backfill from management-scope endpoints).

    Leave blank when the collector is deployed in the management account
    OR the Health-delegated-admin account — it will use its execution role.
    Set when the collector lives in an ops account and needs to reach out
    to mgmt-scope APIs. Follows the same CROSS_ACCOUNT_ROLE_ARN_<ALIAS>
    pattern as the MCP tools; alias is HEALTH.
  EOT
  type        = string
  default     = ""
}
