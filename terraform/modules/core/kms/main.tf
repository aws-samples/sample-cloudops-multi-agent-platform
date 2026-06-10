data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  common_tags = {
    project     = var.project_tag
    environment = var.environment_tag
  }
}

# -----------------------------------------------------------------------------
# Customer-Managed KMS Key — shared across DynamoDB and CloudWatch Logs
# Enables key rotation control and access auditing via CloudTrail.
# -----------------------------------------------------------------------------
resource "aws_kms_key" "platform" {
  description             = "${var.project_tag} platform encryption key"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "RootAccountFullAccess"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid    = "AllowDynamoDBService"
        Effect = "Allow"
        Principal = {
          Service = "dynamodb.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
          "kms:CreateGrant",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      },
      {
        Sid    = "AllowCloudWatchLogsService"
        Effect = "Allow"
        Principal = {
          Service = "logs.${data.aws_region.current.region}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:*"
          }
        }
      },
    ]
  })

  tags = merge(local.common_tags, {
    Name = "${var.project_tag}-platform-key"
  })
}

resource "aws_kms_alias" "platform" {
  name          = "alias/${var.project_tag}-platform"
  target_key_id = aws_kms_key.platform.key_id
}
