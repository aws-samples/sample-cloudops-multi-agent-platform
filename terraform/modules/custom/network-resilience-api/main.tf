# -----------------------------------------------------------------------------
# network-resilience-api — browser-facing REST Lambda for fast compute paths.
#
# Attaches routes to the existing frontend-api API Gateway (passed via
# var.api_gateway_id). Does NOT create a new API Gateway, authorizer, or
# Cognito integration — those are reused from the core frontend-api module.
#
# Gated at the root-module level: only instantiated when
# "network-resiliency-agent" is in var.selected_agents.
# -----------------------------------------------------------------------------

locals {
  common_tags = {
    project     = var.project_tag
    environment = var.environment_tag
  }
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# IAM role + policy
# -----------------------------------------------------------------------------
resource "aws_iam_role" "lambda" {
  name = "${var.project_tag}-network-resilience-api-role"

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

resource "aws_iam_role_policy" "lambda" {
  name = "${var.project_tag}-network-resilience-api-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Effect   = "Allow"
          Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
          Resource = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:*"
        },
        {
          # Read-only access to the AWS network APIs the reassess + live-status
          # routes need. /reassess is pure compute on a supplied topology, but
          # /live-status polls CloudWatch fresh, and cross-account-enrich calls
          # EC2 in spoke accounts.
          Effect = "Allow"
          Action = [
            "cloudwatch:GetMetricData",
            "cloudwatch:ListMetrics",
            "directconnect:DescribeConnections",
            "directconnect:DescribeVirtualInterfaces",
            "ec2:DescribeVpcs",
            "ec2:DescribeTransitGateways",
            "ec2:DescribeTransitGatewayAttachments",
            "ec2:DescribeVpnConnections",
            "sts:GetCallerIdentity",
          ]
          Resource = "*"
        },
      ],
      # Phase 7 cross-account enrichment — granted only for explicitly
      # listed role ARNs so the blast radius of a compromised Lambda is
      # bounded by the operator's configured trust.
      length(var.cross_account_role_arns) > 0 ? [{
        Effect   = "Allow"
        Action   = ["sts:AssumeRole"]
        Resource = var.cross_account_role_arns
      }] : []
    )
  })
}

# -----------------------------------------------------------------------------
# Lambda function
# -----------------------------------------------------------------------------
resource "aws_lambda_function" "api" {
  function_name    = "${var.project_tag}-network-resilience-api"
  role             = aws_iam_role.lambda.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 512
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  environment {
    variables = {
      CROSS_ACCOUNT_ROLE_ARNS = join(",", var.cross_account_role_arns)
    }
  }

  tags = local.common_tags
}

# -----------------------------------------------------------------------------
# API Gateway wiring — attach routes to the EXISTING frontend-api HTTP API.
# -----------------------------------------------------------------------------
resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = var.api_gateway_id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

locals {
  routes = {
    health               = "GET /network-resilience/health"
    reassess             = "POST /network-resilience/reassess"
    live_status          = "POST /network-resilience/live-status"
    utilization          = "POST /network-resilience/utilization"
    cross_account_enrich = "POST /network-resilience/cross-account-enrich"
  }
}

resource "aws_apigatewayv2_route" "routes" {
  for_each = local.routes

  api_id             = var.api_gateway_id
  route_key          = each.value
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = var.cognito_authorizer_id
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvokeNetworkResilience"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.api_gateway_execution_arn}/*/*"
}
