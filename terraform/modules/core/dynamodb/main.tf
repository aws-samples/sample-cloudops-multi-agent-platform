locals {
  common_tags = {
    project     = var.project_tag
    environment = var.environment_tag
  }
}

data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# Resource-Based Policies — defense-in-depth restricting DynamoDB access to
# principals within this account only. Prevents cross-account access even if
# an identity policy is misconfigured.
# -----------------------------------------------------------------------------
resource "aws_dynamodb_resource_policy" "report_prompts" {
  resource_arn = aws_dynamodb_table.report_prompts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyExternalAccess"
        Effect    = "Deny"
        Principal = "*"
        Action    = "dynamodb:*"
        Resource  = aws_dynamodb_table.report_prompts.arn
        Condition = {
          StringNotEquals = {
            "aws:PrincipalAccount" = data.aws_caller_identity.current.account_id
          }
        }
      }
    ]
  })
}

resource "aws_dynamodb_resource_policy" "agent_registry" {
  resource_arn = aws_dynamodb_table.agent_registry.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyExternalAccess"
        Effect    = "Deny"
        Principal = "*"
        Action    = "dynamodb:*"
        Resource  = aws_dynamodb_table.agent_registry.arn
        Condition = {
          StringNotEquals = {
            "aws:PrincipalAccount" = data.aws_caller_identity.current.account_id
          }
        }
      }
    ]
  })
}

resource "aws_dynamodb_resource_policy" "report_templates" {
  resource_arn = aws_dynamodb_table.report_templates.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyExternalAccess"
        Effect    = "Deny"
        Principal = "*"
        Action    = "dynamodb:*"
        Resource  = aws_dynamodb_table.report_templates.arn
        Condition = {
          StringNotEquals = {
            "aws:PrincipalAccount" = data.aws_caller_identity.current.account_id
          }
        }
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# Report Prompts Table
# Partition key: prompt_id (S), GSI on user_id (S)
# On-demand capacity, point-in-time recovery enabled
# -----------------------------------------------------------------------------
resource "aws_dynamodb_table" "report_prompts" {
  name         = var.report_prompts_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "prompt_id"

  attribute {
    name = "prompt_id"
    type = "S"
  }

  attribute {
    name = "user_id"
    type = "S"
  }

  global_secondary_index {
    name            = "user_id-index"
    hash_key        = "user_id"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }

  tags = merge(local.common_tags, {
    Name = var.report_prompts_table_name
  })
}

# -----------------------------------------------------------------------------
# Agent Registry Table
# Partition key: agent_name (S)
# On-demand capacity
# -----------------------------------------------------------------------------
resource "aws_dynamodb_table" "agent_registry" {
  name         = var.agent_registry_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "agent_name"

  attribute {
    name = "agent_name"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }

  tags = merge(local.common_tags, {
    Name = var.agent_registry_table_name
  })
}

# -----------------------------------------------------------------------------
# Report Templates Table
# Partition key: userId (S), Sort key: templateId (S)
# Stores user-created report templates and generated report state
# On-demand capacity, point-in-time recovery enabled
# -----------------------------------------------------------------------------
resource "aws_dynamodb_table" "report_templates" {
  name         = "${var.project_tag}-${var.environment_tag}-${var.report_templates_table_name}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "userId"
  range_key    = "templateId"

  attribute {
    name = "userId"
    type = "S"
  }

  attribute {
    name = "templateId"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }

  tags = merge(local.common_tags, {
    Name = "${var.project_tag}-${var.environment_tag}-${var.report_templates_table_name}"
  })
}
