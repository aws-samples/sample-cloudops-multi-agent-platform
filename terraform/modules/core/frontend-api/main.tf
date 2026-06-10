locals {
  common_tags = {
    project     = var.project_tag
    environment = var.environment_tag
  }
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# IAM Role for Frontend API Lambda
# -----------------------------------------------------------------------------
resource "aws_iam_role" "lambda" {
  name = "${var.project_tag}-frontend-api-role"

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
  name = "${var.project_tag}-frontend-api-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:*"
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:ListSessions",
          "bedrock-agentcore:ListEvents",
          "bedrock-agentcore:DeleteEvent",
        ]
        Resource = var.agentcore_memory_id != "" ? [
          "arn:aws:bedrock-agentcore:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:memory/${var.agentcore_memory_id}"
        ] : ["*"]
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
        ]
        # Scoped to the reports/templates table ONLY. No wildcard fallback —
        # this module is gated on deploy_agents=true (see main.tf count), when
        # report_table_arn is always populated. The old `table/*` branch was
        # dead code that would have granted the user-facing API Lambda write
        # access to every table, including the agent registry.
        Resource = [var.report_table_arn]
      },
      # The reports/templates table is encrypted with the platform CMK, so any
      # GetItem/Query/PutItem against it requires kms:Decrypt (reads) and
      # kms:GenerateDataKey (writes) on that key. Without this, every template
      # list / report read fails closed with KMS AccessDeniedException and the
      # report flow 500s while freeform chat (supervisor path) still works.
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = [var.kms_key_arn]
      },
    ]
  })
}

# -----------------------------------------------------------------------------
# CloudWatch Log Group (pre-created with explicit retention)
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "frontend_api" {
  name              = "/aws/lambda/${var.project_tag}-frontend-api"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

# -----------------------------------------------------------------------------
# Lambda Function
# -----------------------------------------------------------------------------
resource "aws_lambda_function" "frontend_api" {
  function_name    = "${var.project_tag}-frontend-api"
  role             = aws_iam_role.lambda.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  depends_on = [aws_cloudwatch_log_group.frontend_api]

  environment {
    variables = {
      AGENTCORE_MEMORY_ID = var.agentcore_memory_id
      REPORT_TABLE_NAME   = var.report_table_name
    }
  }

  tags = local.common_tags
}

# -----------------------------------------------------------------------------
# API Gateway HTTP API with Cognito JWT Authorizer
# -----------------------------------------------------------------------------
resource "aws_apigatewayv2_api" "frontend" {
  name          = "${var.project_tag}-frontend-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = var.allowed_origins
    allow_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    allow_headers = ["Content-Type", "Authorization"]
    max_age       = 86400
  }

  tags = local.common_tags
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.frontend.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "${var.project_tag}-cognito-jwt"

  jwt_configuration {
    audience = [var.cognito_app_client_id]
    issuer   = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${var.cognito_user_pool_id}"
  }
}

# Lambda integration
resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.frontend.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.frontend_api.invoke_arn
  payload_format_version = "2.0"
}

# Routes — all go to the same Lambda, which routes internally
resource "aws_apigatewayv2_route" "sessions_list" {
  api_id             = aws_apigatewayv2_api.frontend.id
  route_key          = "GET /sessions"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "session_history" {
  api_id             = aws_apigatewayv2_api.frontend.id
  route_key          = "GET /sessions/{id}/history"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "session_delete" {
  api_id             = aws_apigatewayv2_api.frontend.id
  route_key          = "DELETE /sessions/{id}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "templates_list" {
  api_id             = aws_apigatewayv2_api.frontend.id
  route_key          = "GET /templates"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "templates_create" {
  api_id             = aws_apigatewayv2_api.frontend.id
  route_key          = "POST /templates"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "templates_update" {
  api_id             = aws_apigatewayv2_api.frontend.id
  route_key          = "PUT /templates/{id}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "templates_delete" {
  api_id             = aws_apigatewayv2_api.frontend.id
  route_key          = "DELETE /templates/{id}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "reports_list" {
  api_id             = aws_apigatewayv2_api.frontend.id
  route_key          = "GET /reports"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "report_get" {
  api_id             = aws_apigatewayv2_api.frontend.id
  route_key          = "GET /reports/{id}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "report_status" {
  api_id             = aws_apigatewayv2_api.frontend.id
  route_key          = "GET /reports/{id}/status"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "report_delete" {
  api_id             = aws_apigatewayv2_api.frontend.id
  route_key          = "DELETE /reports/{id}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "thread_activity" {
  api_id             = aws_apigatewayv2_api.frontend.id
  route_key          = "GET /threads/{id}/activity"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

# Default stage with auto-deploy
resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.frontend.id
  name        = "$default"
  auto_deploy = true

  tags = local.common_tags
}

# Lambda permission for API Gateway
resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.frontend_api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.frontend.execution_arn}/*/*"
}
