locals {
  common_tags = {
    project     = var.project_tag
    environment = var.environment_tag
    tool        = var.tool_name
  }

  # Pull every ``CROSS_ACCOUNT_ROLE_ARN*`` value out of the tool's env_vars
  # so the IAM policy can grant ``sts:AssumeRole`` on exactly those ARNs.
  # Empty values are skipped (tool runs on the execution role when unset).
  cross_account_role_arns = [
    for k, v in var.env_vars :
    v if(startswith(k, "CROSS_ACCOUNT_ROLE_ARN") && v != "")
  ]
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

resource "aws_iam_role" "lambda" {
  name = "${var.project_tag}-${var.tool_name}-tool-role"

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
  name = "${var.project_tag}-${var.tool_name}-tool-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Effect   = "Allow"
          Action   = var.iam_actions
          Resource = var.iam_resources
        },
        {
          Effect   = "Allow"
          Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
          Resource = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:*"
        },
      ],
      # Scoped sts:AssumeRole — granted only when the tool declares
      # cross-account role ARNs. Resource list is exact ARNs, not "*",
      # so the Lambda can't escalate to assume arbitrary roles.
      length(local.cross_account_role_arns) > 0 ? [
        {
          Effect   = "Allow"
          Action   = ["sts:AssumeRole"]
          Resource = local.cross_account_role_arns
        }
      ] : []
    )
  })
}

# Pre-create the log group with explicit retention so Lambda doesn't create
# one with indefinite retention on first invocation.
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.project_tag}-${var.tool_name}-tool"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

resource "aws_lambda_function" "this" {
  function_name    = "${var.project_tag}-${var.tool_name}-tool"
  role             = aws_iam_role.lambda.arn
  handler          = var.handler
  runtime          = var.runtime
  timeout          = var.timeout
  memory_size      = var.memory_size
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  tags             = local.common_tags

  depends_on = [aws_cloudwatch_log_group.lambda]

  dynamic "environment" {
    for_each = length(var.env_vars) > 0 ? [1] : []
    content {
      variables = var.env_vars
    }
  }
}
