resource "aws_bedrockagentcore_workload_identity" "this" {
  name = "${var.agent_name}-identity"

  allowed_scopes = ["agentcore:invoke", "agentcore:mcp"]

  tags = {
    project     = var.project_tag
    environment = var.environment_tag
    agent_name  = var.agent_name
  }
}
