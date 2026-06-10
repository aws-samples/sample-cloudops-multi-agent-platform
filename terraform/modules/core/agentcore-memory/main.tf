# -----------------------------------------------------------------------------
# AgentCore Memory Resource (native Terraform resource)
#
# Creates the memory resource and optionally LTM strategies.
# The memory_id output is passed to agent runtimes via environment variables.
# -----------------------------------------------------------------------------

resource "aws_bedrockagentcore_memory" "this" {
  name                  = replace("${var.project_tag}_${var.environment_tag}_memory", "-", "_")
  description           = "CloudOps conversation memory (${var.project_tag}-${var.environment_tag})"
  event_expiry_duration = var.event_expiry_duration

  tags = {
    Project     = var.project_tag
    Environment = var.environment_tag
    ManagedBy   = "Terraform"
  }

  timeouts {
    create = "10m"
    delete = "10m"
  }
}

# --- LTM Strategies ---
# Non-CUSTOM types (SUMMARIZATION, USER_PREFERENCE) use built-in logic
# and must NOT include a configuration block.

resource "aws_bedrockagentcore_memory_strategy" "session_summarizer" {
  count = var.enable_ltm_strategies ? 1 : 0

  memory_id  = aws_bedrockagentcore_memory.this.id
  name       = "SessionSummarizer"
  type       = "SUMMARIZATION"
  namespaces = ["/summaries/{actorId}/{sessionId}"]
}

resource "aws_bedrockagentcore_memory_strategy" "preference_learner" {
  count = var.enable_ltm_strategies ? 1 : 0

  memory_id  = aws_bedrockagentcore_memory.this.id
  name       = "PreferenceLearner"
  type       = "USER_PREFERENCE"
  namespaces = ["/preferences/{actorId}"]
}
