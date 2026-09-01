locals {
  common_tags = {
    project     = var.project_tag
    environment = var.environment_tag
    integration = "devops-agent"
  }

  agent_space_arn       = "arn:aws:aidevops:${var.agent_space_region}:${data.aws_caller_identity.current.account_id}:agentspace/${var.agent_space_id}"
  health_stream_enabled = var.automatic_health_enabled && var.health_table_stream_arn != ""
  function_config = merge({
    event = {
      handler = "handler.event_handler"
      timeout = 120
    }
    reconcile = {
      handler = "handler.reconcile_handler"
      timeout = 300
    }
    }, local.health_stream_enabled ? {
    stream = {
      handler = "handler.stream_handler"
      timeout = 120
    }
  } : {})
  workflow_dynamodb_actions = {
    stream = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:TransactWriteItems",
      "dynamodb:UpdateItem",
    ]
    event = [
      "dynamodb:DeleteItem",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:TransactWriteItems",
      "dynamodb:UpdateItem",
    ]
    reconcile = [
      "dynamodb:DeleteItem",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:TransactWriteItems",
      "dynamodb:UpdateItem",
    ]
  }
  workflow_env = {
    INVESTIGATIONS_TABLE_NAME               = aws_dynamodb_table.investigations.name
    DEVOPS_AGENT_SPACE_ID                   = var.agent_space_id
    DEVOPS_AGENT_SPACE_REGION               = var.agent_space_region
    DEVOPS_AGENT_INTEGRATION_ENABLED        = tostring(var.integration_enabled)
    DEVOPS_AGENT_HEALTH_AUTOMATIC_ENABLED   = tostring(var.automatic_health_enabled)
    DEVOPS_AGENT_MAX_CONCURRENCY            = tostring(var.max_concurrency)
    DEVOPS_AGENT_AUTOMATIC_DAILY_BUDGET     = tostring(var.automatic_daily_budget)
    DEVOPS_AGENT_MAX_AGE_MINUTES            = tostring(var.max_age_minutes)
    DEVOPS_AGENT_RECONCILIATION_SECONDS     = tostring(var.sweep_interval_minutes * 60)
    DEVOPS_AGENT_COVERAGE_CACHE_TTL_SECONDS = tostring(var.coverage_cache_ttl_seconds)
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "aws_dynamodb_table" "investigations" {
  name         = "${var.project_tag}-devops-agent-investigations"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "providerTaskId"
    type = "S"
  }

  attribute {
    name = "workflowState"
    type = "S"
  }

  attribute {
    name = "nextReconcileAt"
    type = "S"
  }

  global_secondary_index {
    name            = "TaskIdIndex"
    hash_key        = "providerTaskId"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "WorkIndex"
    hash_key        = "workflowState"
    range_key       = "nextReconcileAt"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  dynamic "server_side_encryption" {
    for_each = var.kms_key_arn != "" ? [1] : []
    content {
      enabled     = true
      kms_key_arn = var.kms_key_arn
    }
  }

  tags = merge(local.common_tags, {
    Name = "${var.project_tag}-devops-agent-investigations"
  })
}

resource "aws_sqs_queue" "stream_failure" {
  count                     = local.health_stream_enabled ? 1 : 0
  name                      = "${var.project_tag}-devops-agent-stream-failure"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = local.common_tags
}

resource "aws_sqs_queue" "event_dlq" {
  name                      = "${var.project_tag}-devops-agent-event-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = local.common_tags
}

resource "aws_sqs_queue" "reconcile_dlq" {
  name                      = "${var.project_tag}-devops-agent-reconcile-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = local.common_tags
}

resource "aws_iam_role" "workflow" {
  for_each = local.function_config
  name     = "${var.project_tag}-devops-agent-${each.key}-role"
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

resource "aws_iam_role_policy" "workflow_base" {
  for_each = local.function_config
  name     = "${var.project_tag}-devops-agent-${each.key}-base"
  role     = aws_iam_role.workflow[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid    = "InvestigationTable"
        Effect = "Allow"
        Action = local.workflow_dynamodb_actions[each.key]
        Resource = [
          aws_dynamodb_table.investigations.arn,
          "${aws_dynamodb_table.investigations.arn}/index/*",
        ]
      },
      {
        Sid    = "Logs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "${aws_cloudwatch_log_group.workflow[each.key].arn}:*"
      },
      ], var.kms_key_arn != "" ? [
      {
        Sid    = "InvestigationTableKey"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey",
        ]
        Resource = var.kms_key_arn
      }
    ] : [])
  })
}

resource "aws_iam_role_policy" "stream_source" {
  count = local.health_stream_enabled ? 1 : 0
  name  = "${var.project_tag}-devops-agent-stream-source"
  role  = aws_iam_role.workflow["stream"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadHealthStream"
        Effect = "Allow"
        Action = [
          "dynamodb:DescribeStream",
          "dynamodb:GetRecords",
          "dynamodb:GetShardIterator",
        ]
        Resource = var.health_table_stream_arn
      },
      {
        Sid      = "DiscoverHealthStreams"
        Effect   = "Allow"
        Action   = ["dynamodb:ListStreams"]
        Resource = "*"
      },
      {
        Sid      = "StreamFailureDestination"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.stream_failure[0].arn
      },
    ]
  })
}

resource "aws_iam_role_policy" "aidevops" {
  for_each = merge({
    event = [
      "aidevops:GetBacklogTask",
      "aidevops:ListJournalRecords",
      "aidevops:UpdateBacklogTask",
    ]
    reconcile = [
      "aidevops:CreateBacklogTask",
      "aidevops:GetBacklogTask",
      "aidevops:ListAssociations",
      "aidevops:ListJournalRecords",
      "aidevops:UpdateBacklogTask",
    ]
    }, local.health_stream_enabled ? {
    stream = [
      "aidevops:CreateBacklogTask",
      "aidevops:ListAssociations",
      "aidevops:ListJournalRecords",
    ]
  } : {})
  name = "${var.project_tag}-devops-agent-${each.key}-provider"
  role = aws_iam_role.workflow[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "ExactAgentSpace"
      Effect   = "Allow"
      Action   = each.value
      Resource = local.agent_space_arn
    }]
  })
}

resource "aws_cloudwatch_log_group" "workflow" {
  for_each          = local.function_config
  name              = "/aws/lambda/${var.project_tag}-devops-agent-${each.key}"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

resource "aws_s3_object" "workflow" {
  bucket      = var.artifact_bucket
  key         = "deployment-artifacts/${var.project_tag}/devops-agent/${filesha256(var.workflow_zip_path)}.zip"
  source      = var.workflow_zip_path
  source_hash = filebase64sha256(var.workflow_zip_path)
  tags        = local.common_tags
}

resource "aws_lambda_function" "workflow" {
  for_each          = local.function_config
  function_name     = "${var.project_tag}-devops-agent-${each.key}"
  role              = aws_iam_role.workflow[each.key].arn
  handler           = each.value.handler
  runtime           = "python3.12"
  timeout           = each.value.timeout
  memory_size       = 512
  s3_bucket         = aws_s3_object.workflow.bucket
  s3_key            = aws_s3_object.workflow.key
  s3_object_version = aws_s3_object.workflow.version_id
  source_code_hash  = filebase64sha256(var.workflow_zip_path)

  environment {
    variables = local.workflow_env
  }

  depends_on = [aws_cloudwatch_log_group.workflow]
  tags       = local.common_tags
}

resource "aws_lambda_event_source_mapping" "health_stream" {
  count                          = local.health_stream_enabled ? 1 : 0
  event_source_arn               = var.health_table_stream_arn
  function_name                  = aws_lambda_function.workflow["stream"].arn
  starting_position              = "TRIM_HORIZON"
  batch_size                     = 10
  maximum_retry_attempts         = 3
  maximum_record_age_in_seconds  = 3600
  bisect_batch_on_function_error = true
  function_response_types        = ["ReportBatchItemFailures"]
  parallelization_factor         = 1

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.stream_failure[0].arn
    }
  }
}

resource "aws_cloudwatch_event_rule" "provider_completion" {
  name        = "${var.project_tag}-devops-agent-completion"
  description = "Reconcile investigation completion and mitigation lifecycle events"
  event_pattern = jsonencode({
    source = ["aws.aidevops"]
    detail-type = [
      "Investigation Completed",
      "Investigation Failed",
      "Investigation Timed Out",
      "Investigation Cancelled",
      "Investigation Skipped",
      "Investigation Linked",
      "Mitigation In Progress",
      "Mitigation Completed",
      "Mitigation Failed",
      "Mitigation Timed Out",
      "Mitigation Cancelled",
    ]
    detail = {
      metadata = {
        agent_space_id = [var.agent_space_id]
      }
      data = {
        task_type = ["INVESTIGATION"]
      }
    }
  })
  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "provider_completion" {
  rule      = aws_cloudwatch_event_rule.provider_completion.name
  target_id = "DevOpsAgentCompletion"
  arn       = aws_lambda_function.workflow["event"].arn

  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 3
  }

  dead_letter_config {
    arn = aws_sqs_queue.event_dlq.arn
  }
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowDevOpsAgentEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.workflow["event"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.provider_completion.arn
}

resource "aws_sqs_queue_policy" "event_dlq" {
  queue_url = aws_sqs_queue.event_dlq.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.event_dlq.arn
      Condition = {
        ArnEquals = {
          "aws:SourceArn" = aws_cloudwatch_event_rule.provider_completion.arn
        }
      }
    }]
  })
}

resource "aws_iam_role" "scheduler" {
  name = "${var.project_tag}-devops-agent-scheduler-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${var.project_tag}-devops-agent-scheduler"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.workflow["reconcile"].arn
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.reconcile_dlq.arn
      },
    ]
  })
}

resource "aws_scheduler_schedule" "reconcile" {
  name                = "${var.project_tag}-devops-agent-reconcile"
  schedule_expression = "rate(${var.sweep_interval_minutes} minutes)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.workflow["reconcile"].arn
    role_arn = aws_iam_role.scheduler.arn

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 3
    }

    dead_letter_config {
      arn = aws_sqs_queue.reconcile_dlq.arn
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each            = local.function_config
  alarm_name          = "${var.project_tag}-devops-agent-${each.key}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    FunctionName = aws_lambda_function.workflow[each.key].function_name
  }
  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  for_each = merge({
    event = aws_sqs_queue.event_dlq.name
    sweep = aws_sqs_queue.reconcile_dlq.name
    }, local.health_stream_enabled ? {
    stream = aws_sqs_queue.stream_failure[0].name
  } : {})
  alarm_name          = "${var.project_tag}-devops-agent-${each.key}-dlq"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    QueueName = each.value
  }
  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "stream_iterator_age" {
  count               = local.health_stream_enabled ? 1 : 0
  alarm_name          = "${var.project_tag}-devops-agent-stream-iterator-age"
  namespace           = "AWS/Lambda"
  metric_name         = "IteratorAge"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 60000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    FunctionName = aws_lambda_function.workflow["stream"].function_name
  }
  tags = local.common_tags
}
