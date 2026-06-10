locals {
  common_tags = {
    project     = var.project_tag
    environment = var.environment_tag
  }
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# IAM Role for AgentCore Gateway
# -----------------------------------------------------------------------------
resource "aws_iam_role" "gateway" {
  name = "${var.project_tag}-agentcore-gateway-role"

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
    Name = "${var.project_tag}-agentcore-gateway-role"
  })
}

resource "aws_iam_role_policy" "gateway" {
  name = "${var.project_tag}-agentcore-gateway-policy"
  role = aws_iam_role.gateway.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = "arn:aws:lambda:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:function:${var.project_tag}-*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# AgentCore Gateway — supports IAM (default) or OAuth (Cognito JWT) auth
# -----------------------------------------------------------------------------
resource "aws_bedrockagentcore_gateway" "this" {
  name            = "${var.project_tag}-gateway"
  protocol_type   = "MCP"
  authorizer_type = var.gateway_auth == "oauth" ? "CUSTOM_JWT" : "AWS_IAM"
  role_arn        = aws_iam_role.gateway.arn

  dynamic "authorizer_configuration" {
    for_each = var.gateway_auth == "oauth" ? [1] : []
    content {
      custom_jwt_authorizer {
        discovery_url    = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${var.cognito_user_pool_id}/.well-known/openid-configuration"
        allowed_audience = [var.cognito_app_client_id]
      }
    }
  }

  tags = merge(local.common_tags, {
    Name = "${var.project_tag}-agentcore-gateway"
  })
}

# -----------------------------------------------------------------------------
# Lambda Tool Targets
# -----------------------------------------------------------------------------
resource "aws_bedrockagentcore_gateway_target" "lambda_tools" {
  for_each = var.lambda_tool_arns

  gateway_identifier = aws_bedrockagentcore_gateway.this.gateway_id
  name               = each.key

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = each.value
        tool_schema {
          inline_payload {
            name        = each.key
            description = "Lambda tool: ${each.key}"
            input_schema {
              type = "object"
            }
            output_schema {
              type = "object"
            }
          }
        }
      }
    }
  }
}
