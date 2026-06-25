locals {
  common_tags = {
    project     = var.project_tag
    environment = var.environment_tag
  }
}

# -----------------------------------------------------------------------------
# Cognito User Pool
# -----------------------------------------------------------------------------
resource "aws_cognito_user_pool" "main" {
  name = "${var.project_tag}-user-pool"

  auto_verified_attributes = ["email"]
  username_attributes      = ["email"]

  # MFA — optional by default (users can enable TOTP)
  mfa_configuration = "OPTIONAL"

  software_token_mfa_configuration {
    enabled = true
  }

  # Disable self-service registration — admin-created users only
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true

    string_attribute_constraints {
      min_length = 1
      max_length = 256
    }
  }

  tags = merge(local.common_tags, {
    Name = "${var.project_tag}-user-pool"
  })
}

# -----------------------------------------------------------------------------
# Cognito User Pool Domain
# -----------------------------------------------------------------------------
resource "aws_cognito_user_pool_domain" "main" {
  domain       = "${var.project_tag}-${var.environment_tag}"
  user_pool_id = aws_cognito_user_pool.main.id
}

data "aws_region" "current" {}

# -----------------------------------------------------------------------------
# Cognito User Pool Client
# -----------------------------------------------------------------------------
resource "aws_cognito_user_pool_client" "main" {
  name         = "${var.project_tag}-app-client"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = var.generate_secret

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  # Base scopes the AG-UI frontend needs. `extra_oauth_scopes` is appended only
  # for the Quick (oauth) path: Amazon Quick reads Cognito's OIDC discovery,
  # which advertises scopes_supported = [openid, email, phone, profile], and
  # requests ALL of them ("phone openid profile email"). The app client must
  # allow every scope Quick requests or Cognito's authorize endpoint returns
  # invalid_scope — hence "phone" gets added for oauth even though the app
  # itself doesn't use it. For the iam/frontend path it stays out, so an
  # existing AG-UI deployment sees no scope change.
  allowed_oauth_scopes = concat(["openid", "email", "profile"], var.extra_oauth_scopes)

  supported_identity_providers = ["COGNITO"]

  callback_urls = var.callback_urls
  logout_urls   = var.logout_urls

  explicit_auth_flows = [
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_USER_PASSWORD_AUTH",
  ]
}
