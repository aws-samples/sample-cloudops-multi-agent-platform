locals {
  common_tags = {
    project     = var.project_tag
    environment = var.environment_tag
    integration = "devops-agent-read"
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "aws_iam_role" "lambda" {
  name = "${var.project_tag}-devops-agent-read-role"
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
  name = "${var.project_tag}-devops-agent-read-policy"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid      = "InvestigationReadOnly"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = var.investigations_table_arn
      },
      {
        Sid      = "RefreshInvestigation"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = var.reconcile_function_arn
      },
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.api.arn}:*"
      },
      ], var.kms_key_arn != "" ? [{
        Sid      = "InvestigationTableDecrypt"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = var.kms_key_arn
      }] : []
    )
  })
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${var.project_tag}-devops-agent-read"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

resource "aws_lambda_function" "api" {
  function_name    = "${var.project_tag}-devops-agent-read"
  role             = aws_iam_role.lambda.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 28
  memory_size      = 256
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  environment {
    variables = {
      INVESTIGATIONS_TABLE_NAME           = var.investigations_table_name
      DEVOPS_AGENT_SPACE_ID               = var.agent_space_id
      DEVOPS_AGENT_RECONCILE_FUNCTION_ARN = var.reconcile_function_arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.api]
  tags       = local.common_tags
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = var.api_gateway_id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "investigation" {
  api_id             = var.api_gateway_id
  route_key          = "GET /devops-agent/investigations/{investigationId}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = var.cognito_authorizer_id
}

resource "aws_apigatewayv2_route" "investigation_refresh" {
  api_id             = var.api_gateway_id
  route_key          = "POST /devops-agent/investigations/{investigationId}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = var.cognito_authorizer_id
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvokeDevOpsAgentRead"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.api_gateway_execution_arn}/*/GET/devops-agent/investigations/*"
}

resource "aws_lambda_permission" "apigw_refresh" {
  statement_id  = "AllowAPIGatewayRefreshInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.api_gateway_execution_arn}/*/POST/devops-agent/investigations/*"
}
