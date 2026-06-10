locals {
  common_tags = {
    project     = var.project_tag
    environment = var.environment_tag
  }
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# SNS Topic for Alarm Notifications
# -----------------------------------------------------------------------------
resource "aws_sns_topic" "alerts" {
  name = "${var.project_tag}-alerts"

  tags = merge(local.common_tags, {
    Name = "${var.project_tag}-alerts"
  })
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# -----------------------------------------------------------------------------
# Runtime log groups are auto-created by AgentCore as
# `/aws/bedrock-agentcore/runtimes/<name>-DEFAULT` and `<name>-<endpoint>`.
# Customer-controlled vending happens in the APPLICATION_LOGS delivery below.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# CloudWatch Alarm — Agent Latency (p99 > threshold)
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "agent_latency" {
  alarm_name          = "${var.project_tag}-agent-latency-p99"
  alarm_description   = "Agent invocation latency p99 exceeds ${var.latency_threshold_seconds}s"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "AgentInvocationLatency"
  namespace           = "Bedrock-AgentCore"
  period              = 60
  extended_statistic  = "p99"
  threshold           = var.latency_threshold_seconds * 1000 # convert to milliseconds
  treat_missing_data  = "notBreaching"

  dimensions = {
    Project = var.project_tag
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = merge(local.common_tags, {
    Name = "${var.project_tag}-agent-latency-alarm"
  })
}

# -----------------------------------------------------------------------------
# CloudWatch Dashboard — Agent Metrics
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_dashboard" "agents" {
  dashboard_name = "${var.project_tag}-agent-dashboard"

  dashboard_body = jsonencode({
    widgets = concat(
      [
        {
          type   = "metric"
          x      = 0
          y      = 0
          width  = 12
          height = 6
          properties = {
            title   = "Agent Invocation Count"
            metrics = [["Bedrock-AgentCore", "AgentInvocationCount", "Project", var.project_tag]]
            period  = 300
            stat    = "Sum"
            region  = data.aws_region.current.region
            view    = "timeSeries"
          }
        },
        {
          type   = "metric"
          x      = 12
          y      = 0
          width  = 12
          height = 6
          properties = {
            title = "Agent Latency (p50, p95, p99)"
            metrics = [
              ["Bedrock-AgentCore", "AgentInvocationLatency", "Project", var.project_tag, { stat = "p50", label = "p50" }],
              ["...", { stat = "p95", label = "p95" }],
              ["...", { stat = "p99", label = "p99" }],
            ]
            period = 300
            region = data.aws_region.current.region
            view   = "timeSeries"
          }
        },
        {
          type   = "metric"
          x      = 0
          y      = 6
          width  = 12
          height = 6
          properties = {
            title = "Tool Call Success / Error Rate"
            metrics = [
              ["Bedrock-AgentCore", "ToolCallSuccessCount", "Project", var.project_tag, { stat = "Sum", label = "Success" }],
              ["Bedrock-AgentCore", "ToolCallErrorCount", "Project", var.project_tag, { stat = "Sum", label = "Error" }],
            ]
            period = 300
            region = data.aws_region.current.region
            view   = "timeSeries"
          }
        },
        {
          type   = "metric"
          x      = 12
          y      = 6
          width  = 12
          height = 6
          properties = {
            title   = "Sub-Agent Timeout Count"
            metrics = [["Bedrock-AgentCore", "SubAgentTimeoutCount", "Project", var.project_tag]]
            period  = 300
            stat    = "Sum"
            region  = data.aws_region.current.region
            view    = "timeSeries"
          }
        },
        # --- GenAI Observability Widgets (AgentCore Observability) ---
        {
          type   = "metric"
          x      = 0
          y      = 12
          width  = 12
          height = 6
          properties = {
            title = "Token Usage by Agent (Input/Output)"
            metrics = [
              ["Bedrock-AgentCore", "InputTokenCount", "Project", var.project_tag, { stat = "Sum", label = "Input Tokens" }],
              ["Bedrock-AgentCore", "OutputTokenCount", "Project", var.project_tag, { stat = "Sum", label = "Output Tokens" }],
            ]
            period = 300
            region = data.aws_region.current.region
            view   = "timeSeries"
          }
        },
        {
          type   = "metric"
          x      = 12
          y      = 12
          width  = 12
          height = 6
          properties = {
            title = "Model Invocation Latency by Agent"
            metrics = [
              ["Bedrock-AgentCore", "ModelInvocationLatency", "Project", var.project_tag, { stat = "p50", label = "p50" }],
              ["...", { stat = "p95", label = "p95" }],
              ["...", { stat = "p99", label = "p99" }],
            ]
            period = 300
            region = data.aws_region.current.region
            view   = "timeSeries"
          }
        },
      ],
    )
  })
}

# -----------------------------------------------------------------------------
# X-Ray Resource Policy (allows X-Ray to write to CloudWatch Logs)
# -----------------------------------------------------------------------------
# The runtime-side ADOT permissions live inline on the agent execution roles
# in agent-runtime-base/main.tf and agentcore-runtime/main.tf.
resource "aws_cloudwatch_log_resource_policy" "xray" {
  policy_name = "${var.project_tag}-xray-transaction-search"
  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "TransactionSearchXRayAccess"
        Effect    = "Allow"
        Principal = { Service = "xray.amazonaws.com" }
        Action    = "logs:PutLogEvents"
        Resource = [
          "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:aws/spans:*",
          "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/application-signals/data:*",
        ]
        Condition = {
          ArnLike      = { "aws:SourceArn" = "arn:aws:xray:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:*" }
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# X-Ray Transaction Search Config (enables CloudWatchLogs as trace destination)
# -----------------------------------------------------------------------------
# No native Terraform resource exists for AWS::XRay::TransactionSearchConfig.
# The AWS CLI call is idempotent — succeeds whether already enabled or not.
# We use a null_resource so it runs once and doesn't re-run on every apply.
resource "null_resource" "xray_transaction_search" {
  triggers = {
    # Only re-run if the resource policy changes (i.e., first deploy or policy update)
    policy_id = aws_cloudwatch_log_resource_policy.xray.id
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws xray update-trace-segment-destination --destination CloudWatchLogs \
        --region ${data.aws_region.current.region}
      # Wait for status to become ACTIVE (up to 90s)
      for i in $(seq 1 18); do
        STATUS=$(aws xray get-trace-segment-destination \
          --region ${data.aws_region.current.region} \
          --query 'Status' --output text 2>/dev/null)
        [ "$STATUS" = "ACTIVE" ] && exit 0
        sleep 5
      done
      echo "WARNING: X-Ray Transaction Search status is still not ACTIVE after 90s"
    EOT
  }

  depends_on = [aws_cloudwatch_log_resource_policy.xray]
}

# -----------------------------------------------------------------------------
# Trace Delivery — Runtime → X-Ray
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_log_delivery_source" "runtime_traces" {
  count        = var.enable_runtime_tracing ? 1 : 0
  name         = "${var.project_tag}-runtime-traces"
  log_type     = "TRACES"
  resource_arn = var.runtime_arn
}

resource "aws_cloudwatch_log_delivery_destination" "runtime_traces_xray" {
  count                     = var.enable_runtime_tracing ? 1 : 0
  name                      = "${var.project_tag}-runtime-traces-dest"
  delivery_destination_type = "XRAY"
}

resource "aws_cloudwatch_log_delivery" "runtime_traces" {
  count                    = var.enable_runtime_tracing ? 1 : 0
  delivery_source_name     = aws_cloudwatch_log_delivery_source.runtime_traces[0].name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.runtime_traces_xray[0].arn

  depends_on = [null_resource.xray_transaction_search]
}

# -----------------------------------------------------------------------------
# Application Logs — Runtime → CloudWatch Logs (vended logs)
# -----------------------------------------------------------------------------
# Vends the supervisor runtime's stdout/stderr to a customer-controlled log
# group with known retention. The auto-created `/aws/bedrock-agentcore/runtimes/*`
# groups keep working too — this pipeline is what the GenAI Observability
# dashboard correlates against.
resource "aws_cloudwatch_log_group" "runtime_logs" {
  count             = var.enable_runtime_tracing ? 1 : 0
  name              = "/aws/vendedlogs/bedrock-agentcore/runtime/${var.project_tag}"
  retention_in_days = var.log_retention_days

  tags = merge(local.common_tags, {
    Name = "${var.project_tag}-runtime-logs"
  })
}

resource "aws_cloudwatch_log_delivery_source" "runtime_logs" {
  count        = var.enable_runtime_tracing ? 1 : 0
  name         = "${var.project_tag}-runtime-logs"
  log_type     = "APPLICATION_LOGS"
  resource_arn = var.runtime_arn
}

resource "aws_cloudwatch_log_delivery_destination" "runtime_logs_cwl" {
  count                     = var.enable_runtime_tracing ? 1 : 0
  name                      = "${var.project_tag}-runtime-logs-dest"
  delivery_destination_type = "CWL"

  delivery_destination_configuration {
    destination_resource_arn = aws_cloudwatch_log_group.runtime_logs[0].arn
  }
}

resource "aws_cloudwatch_log_delivery" "runtime_logs" {
  count                    = var.enable_runtime_tracing ? 1 : 0
  delivery_source_name     = aws_cloudwatch_log_delivery_source.runtime_logs[0].name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.runtime_logs_cwl[0].arn
}

# -----------------------------------------------------------------------------
# Trace Delivery — Gateway → X-Ray
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_log_delivery_source" "gateway_traces" {
  count        = var.enable_gateway_tracing ? 1 : 0
  name         = "${var.project_tag}-gateway-traces"
  log_type     = "TRACES"
  resource_arn = var.gateway_arn
}

resource "aws_cloudwatch_log_delivery_destination" "gateway_traces_xray" {
  count                     = var.enable_gateway_tracing ? 1 : 0
  name                      = "${var.project_tag}-gateway-traces-dest"
  delivery_destination_type = "XRAY"
}

resource "aws_cloudwatch_log_delivery" "gateway_traces" {
  count                    = var.enable_gateway_tracing ? 1 : 0
  delivery_source_name     = aws_cloudwatch_log_delivery_source.gateway_traces[0].name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.gateway_traces_xray[0].arn

  depends_on = [null_resource.xray_transaction_search]
}

# -----------------------------------------------------------------------------
# Application Logs — Gateway → CloudWatch Logs (vended logs)
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "gateway_logs" {
  count             = var.enable_gateway_tracing ? 1 : 0
  name              = "/aws/vendedlogs/bedrock-agentcore/gateway/${var.project_tag}"
  retention_in_days = var.log_retention_days

  tags = merge(local.common_tags, {
    Name = "${var.project_tag}-gateway-logs"
  })
}

resource "aws_cloudwatch_log_delivery_source" "gateway_logs" {
  count        = var.enable_gateway_tracing ? 1 : 0
  name         = "${var.project_tag}-gateway-logs"
  log_type     = "APPLICATION_LOGS"
  resource_arn = var.gateway_arn
}

resource "aws_cloudwatch_log_delivery_destination" "gateway_logs_cwl" {
  count                     = var.enable_gateway_tracing ? 1 : 0
  name                      = "${var.project_tag}-gateway-logs-dest"
  delivery_destination_type = "CWL"

  delivery_destination_configuration {
    destination_resource_arn = aws_cloudwatch_log_group.gateway_logs[0].arn
  }
}

resource "aws_cloudwatch_log_delivery" "gateway_logs" {
  count                    = var.enable_gateway_tracing ? 1 : 0
  delivery_source_name     = aws_cloudwatch_log_delivery_source.gateway_logs[0].name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.gateway_logs_cwl[0].arn
}

# -----------------------------------------------------------------------------
# Trace Delivery — Memory → X-Ray
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_log_delivery_source" "memory_traces" {
  count        = var.enable_memory_tracing ? 1 : 0
  name         = "${var.project_tag}-memory-traces"
  log_type     = "TRACES"
  resource_arn = var.memory_arn
}

resource "aws_cloudwatch_log_delivery_destination" "memory_traces_xray" {
  count                     = var.enable_memory_tracing ? 1 : 0
  name                      = "${var.project_tag}-memory-traces-dest"
  delivery_destination_type = "XRAY"
}

resource "aws_cloudwatch_log_delivery" "memory_traces" {
  count                    = var.enable_memory_tracing ? 1 : 0
  delivery_source_name     = aws_cloudwatch_log_delivery_source.memory_traces[0].name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.memory_traces_xray[0].arn

  depends_on = [null_resource.xray_transaction_search]
}
