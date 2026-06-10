# -----------------------------------------------------------------------------
# Agent Runtime Base Module
#
# Creates per-agent AgentCore Runtime resources: IAM role, runtime, endpoint,
# and DynamoDB registry entry. Used by each agent module to avoid duplicating
# runtime creation logic.
# -----------------------------------------------------------------------------

locals {
  common_tags = {
    project     = var.project_tag
    environment = var.environment_tag
    agent       = var.agent_name
  }

  # AgentCore Runtime / Endpoint names MUST match ^[a-zA-Z][a-zA-Z0-9_]{0,47}$.
  # Both the project tag AND the agent name get hyphens replaced with
  # underscores. Previously only the agent name was sanitised — a
  # hyphenated PROJECT_PREFIX (e.g. `cloudops-topology-test` used by the
  # Layer 3 test harness) would produce `cloudops-topology-test_agent_runtime`
  # and fail `terraform apply` with "Invalid Attribute Value Match".
  agent_name_underscored  = replace(var.agent_name, "-", "_")
  project_tag_underscored = replace(var.project_tag, "-", "_")
  runtime_name            = "${local.project_tag_underscored}_${local.agent_name_underscored}_runtime"
  endpoint_name           = "${local.project_tag_underscored}_${local.agent_name_underscored}_endpoint"

  # Domain derived from agent name for OTEL tagging
  domain = (
    var.parent_agent != ""
    ? replace(var.parent_agent, "-agent", "")
    : local.agent_name_underscored
  )

  # Memory ID: passed from the native Terraform memory resource
  effective_memory_id = var.agentcore_memory_id

  # Whether to include memory permissions
  include_memory = var.is_mid_level && local.effective_memory_id != ""

  # Registry table ARN — derive from name if not provided
  registry_table_arn = (
    var.agent_registry_table_arn != ""
    ? var.agent_registry_table_arn
    : "arn:aws:dynamodb:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:table/${var.agent_registry_table_name}"
  )

  # Environment variables for the runtime
  base_env_vars = {
    AGENT_NAME                 = var.agent_name
    AGENT_REGISTRY_TABLE       = var.agent_registry_table_name
    AGENTCORE_GATEWAY_ENDPOINT = var.agentcore_gateway_endpoint
    AWS_REGION                 = data.aws_region.current.region
    AWS_DEFAULT_REGION         = data.aws_region.current.region
  }

  memory_env_vars = local.include_memory ? {
    AGENTCORE_MEMORY_ID = local.effective_memory_id
  } : {}

  model_env_vars = var.bedrock_model_id != "" ? {
    BEDROCK_MODEL_ID = var.bedrock_model_id
  } : {}

  environment_variables = merge(local.base_env_vars, local.memory_env_vars, local.model_env_vars)

  # A2A endpoint URL construction
  runtime_arn_encoded = var.enabled ? urlencode(aws_bedrockagentcore_agent_runtime.this[0].agent_runtime_arn) : ""
  a2a_endpoint        = var.enabled ? "https://bedrock-agentcore.${data.aws_region.current.region}.amazonaws.com/runtimes/${local.runtime_arn_encoded}/invocations?qualifier=${local.endpoint_name}" : ""
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# IAM Role for Agent Runtime Execution
# -----------------------------------------------------------------------------
resource "aws_iam_role" "agent_execution" {
  count = var.enabled ? 1 : 0

  name = "${var.project_tag}-${var.agent_name}-runtime-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = [
            "bedrock.amazonaws.com",
            "bedrock-agentcore.amazonaws.com"
          ]
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = merge(local.common_tags, {
    Name = "${var.project_tag}-${var.agent_name}-runtime-role"
  })
}

resource "aws_iam_role_policy" "agent_execution" {
  count = var.enabled ? 1 : 0

  name = "${var.project_tag}-${var.agent_name}-runtime-policy"
  role = aws_iam_role.agent_execution[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        # Bedrock model invocation (including cross-region inference profiles)
        {
          Effect = "Allow"
          Action = [
            "bedrock:InvokeModel",
            "bedrock:InvokeModelWithResponseStream",
            "bedrock:ApplyGuardrail",
          ]
          Resource = [
            "arn:aws:bedrock:*::foundation-model/*",
            "arn:aws:bedrock:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:inference-profile/*",
            "arn:aws:bedrock:us:${data.aws_caller_identity.current.account_id}:inference-profile/*",
            "arn:aws:bedrock:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:guardrail/*",
          ]
        },
        # STS for cross-account or agent-to-agent calls
        {
          Effect   = "Allow"
          Action   = ["sts:AssumeRole"]
          Resource = "*"
        },
        # ECR image pull
        {
          Effect = "Allow"
          Action = [
            "ecr:GetAuthorizationToken",
            "ecr:BatchGetImage",
            "ecr:GetDownloadUrlForLayer",
          ]
          Resource = "*"
        },
        # CloudWatch logging
        {
          Effect = "Allow"
          Action = [
            "logs:CreateLogGroup",
            "logs:CreateLogStream",
            "logs:PutLogEvents",
          ]
          Resource = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:*"
        },
        # X-Ray trace export (ADOT). GetSamplingRules + GetSamplingTargets
        # are required by aws-opentelemetry-distro's sampler — ADOT refreshes
        # sampling decisions every ~10s via these APIs. Without them spans
        # get silently dropped and never reach aws/spans, which empties out
        # the built-in AWS GenAI Observability dashboard and any future
        # AgentCore Evaluations run. See docs/observability-tuning.md.
        {
          Effect = "Allow"
          Action = [
            "xray:PutTraceSegments",
            "xray:PutTelemetryRecords",
            "xray:GetSamplingRules",
            "xray:GetSamplingTargets",
          ]
          Resource = "*"
        },
        # DynamoDB agent registry read access
        {
          Effect = "Allow"
          Action = [
            "dynamodb:Scan",
            "dynamodb:GetItem",
            "dynamodb:Query",
          ]
          Resource = [local.registry_table_arn]
        },
        # AgentCore Runtime invocation (for A2A calls to other agents)
        {
          Effect = "Allow"
          Action = [
            "bedrock-agentcore:InvokeAgentRuntime",
          ]
          Resource = "arn:aws:bedrock-agentcore:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:runtime/*"
        },
      ],
      # Conditionally add gateway invocation permissions for leaf agents
      !var.is_mid_level && var.agentcore_gateway_arn != "" ? [
        {
          Effect = "Allow"
          Action = [
            "bedrock-agentcore:InvokeGateway",
          ]
          Resource = var.agentcore_gateway_arn
        }
      ] : [],
      # KMS decrypt for the CMK-encrypted agent registry table. Without this,
      # `dynamodb:Scan` on the registry fails with AccessDenied (the read
      # requires decrypting the table), load_agent_registry() swallows the
      # error and falls back to zero children — the agent then reports
      # "no child agents deployed". Required on EVERY agent that reads an
      # encrypted DynamoDB table.
      var.kms_key_arn != "" ? [
        {
          Effect = "Allow"
          Action = [
            "kms:Decrypt",
            "kms:DescribeKey",
          ]
          Resource = var.kms_key_arn
        }
      ] : [],
      # Conditionally add memory permissions for mid-level agents
      local.include_memory ? [
        {
          Effect = "Allow"
          Action = [
            "bedrock-agentcore:CreateMemory",
            "bedrock-agentcore:GetMemory",
            "bedrock-agentcore:InvokeMemory",
            "bedrock-agentcore:ListSessions",
            "bedrock-agentcore:ListEvents",
            "bedrock-agentcore:DeleteSession",
          ]
          Resource = [
            "arn:aws:bedrock-agentcore:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:memory/${local.effective_memory_id}"
          ]
        }
      ] : []
    )
  })
}

# -----------------------------------------------------------------------------
# AgentCore Runtime
# -----------------------------------------------------------------------------
resource "aws_bedrockagentcore_agent_runtime" "this" {
  count = var.enabled ? 1 : 0

  agent_runtime_name = local.runtime_name

  network_configuration {
    network_mode = "PUBLIC"
  }

  agent_runtime_artifact {
    container_configuration {
      container_uri = var.container_image
    }
  }

  # No authorizer_configuration — AWS_IAM is the default for A2A agents.
  # The Supervisor runtime uses CUSTOM_JWT and is managed by the existing
  # agentcore-runtime module, not this base module.

  protocol_configuration {
    server_protocol = "HTTP"
  }

  environment_variables = local.environment_variables

  role_arn = aws_iam_role.agent_execution[0].arn

  tags = merge(local.common_tags, {
    Name = "${var.project_tag}-${var.agent_name}-runtime"
  })
}

# -----------------------------------------------------------------------------
# AgentCore Runtime Endpoint
# -----------------------------------------------------------------------------
resource "aws_bedrockagentcore_agent_runtime_endpoint" "this" {
  count = var.enabled ? 1 : 0

  name                  = local.endpoint_name
  agent_runtime_id      = aws_bedrockagentcore_agent_runtime.this[0].agent_runtime_id
  agent_runtime_version = aws_bedrockagentcore_agent_runtime.this[0].agent_runtime_version

  tags = merge(local.common_tags, {
    Name = "${var.project_tag}-${var.agent_name}-endpoint"
  })
}

# -----------------------------------------------------------------------------
# DynamoDB Registry Entry
# -----------------------------------------------------------------------------
resource "null_resource" "register_agent" {
  count = var.enabled ? 1 : 0

  triggers = {
    agent_name    = var.agent_name
    runtime_arn   = aws_bedrockagentcore_agent_runtime.this[0].agent_runtime_arn
    endpoint_name = local.endpoint_name
    description   = var.agent_description
    parent_agent  = var.parent_agent
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws dynamodb put-item \
        --table-name ${var.agent_registry_table_name} \
        --item '{
          "agent_name": {"S": "${var.agent_name}"},
          "a2a_endpoint": {"S": "${local.a2a_endpoint}"},
          "description": {"S": "${var.agent_description}"},
          "parent_agent": {"S": "${var.parent_agent}"},
          "enabled": {"BOOL": true},
          "deployed_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}
        }' \
        --region ${data.aws_region.current.region}
    EOT
  }

  # Registry cleanup on agent removal is handled by sync_agent_registry()
  # in deploy.sh post_deploy_sync. A destroy-time provisioner can't be used
  # here because adding new trigger keys causes "Missing map element" errors
  # when Terraform destroys the old resource (old state lacks the new keys).
}
