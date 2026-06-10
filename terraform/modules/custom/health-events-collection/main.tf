# Health Events Data Collection Module
#
# Creates:
# - DynamoDB table for storing health events
# - EventBridge rule to capture aws.health events
# - SQS queue to buffer events
# - Lambda collector to process events and store in DynamoDB

locals {
  common_tags = {
    project     = var.project_tag
    environment = var.environment_tag
  }
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# DynamoDB Table
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "health_events" {
  name         = "${var.project_tag}-health-events"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "eventArn"
  range_key    = "accountId"

  attribute {
    name = "eventArn"
    type = "S"
  }

  attribute {
    name = "accountId"
    type = "S"
  }

  attribute {
    name = "eventTypeCategory"
    type = "S"
  }

  attribute {
    name = "lastUpdateTime"
    type = "S"
  }

  global_secondary_index {
    name            = "CategoryTimeIndex"
    hash_key        = "eventTypeCategory"
    range_key       = "lastUpdateTime"
    projection_type = "ALL"
  }

  # AccountTimeIndex — enables efficient per-account time-range queries
  # without scanning the whole table. Query pattern:
  #   accountId = :acct AND lastUpdateTime BETWEEN :from AND :to
  # The primary key (eventArn, accountId) is optimised for per-event
  # lookups; this GSI is for the much more common "what happened in
  # account X recently?" path.
  global_secondary_index {
    name            = "AccountTimeIndex"
    hash_key        = "accountId"
    range_key       = "lastUpdateTime"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = merge(local.common_tags, {
    Name = "${var.project_tag}-health-events"
  })
}

# ---------------------------------------------------------------------------
# SQS Queue (buffers EventBridge health events for the collector Lambda)
# ---------------------------------------------------------------------------
resource "aws_sqs_queue" "health_events" {
  name                       = "${var.project_tag}-health-events-queue"
  visibility_timeout_seconds = 960
  message_retention_seconds  = 1209600 # 14 days
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.health_events_dlq.arn
    maxReceiveCount     = 3
  })

  tags = local.common_tags
}

resource "aws_sqs_queue" "health_events_dlq" {
  name                      = "${var.project_tag}-health-events-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = local.common_tags
}

# Allow EventBridge to send to SQS
resource "aws_sqs_queue_policy" "health_events" {
  queue_url = aws_sqs_queue.health_events.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.health_events.arn
      Condition = {
        ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.health_events.arn }
      }
    }]
  })
}

# ---------------------------------------------------------------------------
# EventBridge Rule (captures AWS Health events)
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "health_events" {
  name        = "${var.project_tag}-health-events"
  description = "Capture AWS Health events for the CloudOps platform"

  event_pattern = jsonencode({
    source      = ["aws.health"]
    detail-type = ["AWS Health Event", "AWS Health Abuse Event"]
  })

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "sqs" {
  rule      = aws_cloudwatch_event_rule.health_events.name
  target_id = "HealthEventSQS"
  arn       = aws_sqs_queue.health_events.arn
}

# ---------------------------------------------------------------------------
# Collector Lambda (processes SQS events → DynamoDB)
# ---------------------------------------------------------------------------
resource "aws_iam_role" "collector" {
  name = "${var.project_tag}-health-events-collector-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "collector" {
  name = "${var.project_tag}-health-events-collector-policy"
  role = aws_iam_role.collector.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
        ]
        Resource = aws_dynamodb_table.health_events.arn
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
        ]
        Resource = aws_sqs_queue.health_events.arn
      },
      {
        # Organizations API for account-name resolution. When
        # CROSS_ACCOUNT_ROLE_ARN_HEALTH is set, the collector assumes
        # that role before calling Organizations, so this local statement
        # is only exercised when no cross-account role is configured.
        Effect   = "Allow"
        Action   = ["organizations:DescribeAccount"]
        Resource = "*"
      },
      {
        # Narrative enrichment via Claude Haiku 4.5. We use the global
        # cross-region inference profile, which fans out to regional
        # foundation-model ARNs — the resource list must cover both the
        # inference profile and the underlying model invocations.
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
          "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/global.anthropic.claude-haiku-4-5-20251001-v1:0",
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:*"
      },
      ],
      # Scoped sts:AssumeRole — granted only when an ops-account collector
      # was told which mgmt-scope role to assume. Same pattern as
      # lambda-tool-base for MCP tools, keeps the blast radius tight.
      var.cross_account_role_arn != "" ? [
        {
          Effect   = "Allow"
          Action   = ["sts:AssumeRole"]
          Resource = [var.cross_account_role_arn]
        }
      ] : []
    )
  })
}

resource "aws_lambda_function" "collector" {
  function_name    = "${var.project_tag}-health-events-collector"
  role             = aws_iam_role.collector.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 900 # 15 min (matches SQS visibility timeout)
  memory_size      = 256
  filename         = var.collector_zip_path
  source_code_hash = filebase64sha256(var.collector_zip_path)

  environment {
    variables = {
      HEALTH_EVENTS_TABLE_NAME = aws_dynamodb_table.health_events.name
      EVENTS_TTL_DAYS          = "180"
      LOG_LEVEL                = "INFO"
      # Claude Haiku 4.5 global cross-region inference profile — set to an
      # empty string in the tfvar `health_events_enrichment_model_id` to
      # disable LLM enrichment. Rules-based fields (riskLevel, accountName)
      # are unaffected.
      ENRICHMENT_MODEL_ID   = var.enrichment_model_id
      ENRICHMENT_TIMEOUT_S  = "5"
      ENRICHMENT_MAX_TOKENS = "300"
      # Cross-account role the collector assumes for Organizations and
      # Health org-view APIs. Empty => use execution role (single-account or
      # mgmt/delegated-admin deploys). See shared/cross_account.py — alias
      # HEALTH maps to this env var via CROSS_ACCOUNT_ROLE_ARN_<ALIAS>.
      CROSS_ACCOUNT_ROLE_ARN_HEALTH = var.cross_account_role_arn
    }
  }

  tags = local.common_tags
}

# SQS trigger for the collector Lambda
resource "aws_lambda_event_source_mapping" "sqs_trigger" {
  event_source_arn = aws_sqs_queue.health_events.arn
  function_name    = aws_lambda_function.collector.arn
  batch_size       = 1
  enabled          = true
}
