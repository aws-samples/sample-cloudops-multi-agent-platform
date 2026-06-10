#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# scripts/lib/auth.sh — AWS authentication primitives
#
# Sourced by scripts/deploy.sh. Depends on: common.sh, shared_config.sh.
#
# Provides:
#   Primary profile:
#     setup_sso_profile, setup_access_key_profile, preflight_auth
#   Cross-account profiles:
#     setup_cross_account_sso_profile, configure_cross_account_target,
#     preflight_cross_account
#   Discovery:
#     auth_sso_cross_account_targets — emits "<suffix>:<ssm_key>:<env_field>:<label>"
# -----------------------------------------------------------------------------

# =============================================================================
# Authentication Helpers
# =============================================================================
# Two auth methods, driven by $AUTH_METHOD in .env:
#   - sso         IAM Identity Center (recommended). Browser login, temp creds.
#   - credentials Long-lived IAM access keys in ~/.aws/credentials.
#
# Either way the rest of the script just uses $AWS_PROFILE — only the way the
# profile gets credentials differs.

# Idempotent. Rewrites the [sso-session] + [profile] blocks if they already
# exist so subsequent setup runs converge.
setup_sso_profile() {
  local session_name="$1"   # e.g. cloudops-sso
  local profile_name="$2"   # e.g. cloudops-dev
  local start_url="$3"
  local sso_region="$4"
  local account_id="$5"
  local role_name="$6"
  local region="$7"

  local aws_config="$HOME/.aws/config"
  mkdir -p "$(dirname "$aws_config")"
  touch "$aws_config"

  local tmp
  tmp=$(mktemp)
  awk -v s="[sso-session $session_name]" -v p="[profile $profile_name]" '
    BEGIN { skip = 0 }
    /^\[/ {
      if ($0 == s || $0 == p) { skip = 1; next }
      else { skip = 0 }
    }
    skip == 0 { print }
  ' "$aws_config" > "$tmp"
  mv "$tmp" "$aws_config"

  cat >> "$aws_config" <<CONFIG

[sso-session $session_name]
sso_start_url = $start_url
sso_region = $sso_region
sso_registration_scopes = sso:account:access

[profile $profile_name]
sso_session = $session_name
sso_account_id = $account_id
sso_role_name = $role_name
region = $region
output = json
CONFIG

  echo "  Wrote SSO profile '$profile_name' (session '$session_name') to $aws_config"
}

# Static-credentials fallback. Keys read interactively (no command history).
setup_access_key_profile() {
  local profile_name="$1"
  local region="$2"

  local aws_creds="$HOME/.aws/credentials"
  local aws_config="$HOME/.aws/config"
  mkdir -p "$(dirname "$aws_creds")"
  touch "$aws_creds" "$aws_config"

  echo ""
  echo "  Enter AWS access keys for profile '$profile_name'."
  echo "  Note: Long-lived keys are less secure than SSO. Prefer 'sso' if your org supports it."
  local access_key_id secret_access_key session_token
  read -r -p "  AWS Access Key ID: " access_key_id
  read -r -s -p "  AWS Secret Access Key: " secret_access_key
  echo ""
  read -r -p "  AWS Session Token (optional): " session_token

  local tmp
  tmp=$(mktemp)
  awk -v p="[$profile_name]" '
    BEGIN { skip = 0 }
    /^\[/ { if ($0 == p) { skip = 1; next } else { skip = 0 } }
    skip == 0 { print }
  ' "$aws_creds" > "$tmp"
  mv "$tmp" "$aws_creds"

  {
    echo ""
    echo "[$profile_name]"
    echo "aws_access_key_id = $access_key_id"
    echo "aws_secret_access_key = $secret_access_key"
    if [ -n "$session_token" ]; then
      echo "aws_session_token = $session_token"
    fi
  } >> "$aws_creds"
  chmod 600 "$aws_creds"

  tmp=$(mktemp)
  awk -v p="[profile $profile_name]" '
    BEGIN { skip = 0 }
    /^\[/ { if ($0 == p) { skip = 1; next } else { skip = 0 } }
    skip == 0 { print }
  ' "$aws_config" > "$tmp"
  mv "$tmp" "$aws_config"

  cat >> "$aws_config" <<CONFIG

[profile $profile_name]
region = $region
output = json
CONFIG

  echo "  Wrote credentials to $aws_creds and region to $aws_config."
}

# Verify $AWS_PROFILE has working credentials. On expired SSO tokens, auto-launch
# `aws sso login` exactly once and retry. Wired into deploy.sh as a replacement
# for validate_credentials when AUTH_METHOD=sso.
preflight_auth() {
  # Setup hasn't completed yet — skip cleanly.
  [ -z "${AWS_PROFILE:-}" ] && return 0

  local sts_output
  if sts_output=$(aws sts get-caller-identity --profile "$AWS_PROFILE" --output json 2>&1); then
    local identity
    identity=$(echo "$sts_output" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin).get("Arn",""))' 2>/dev/null || echo "")
    [ -n "$identity" ] && info "Authenticated as: $identity"
    return 0
  fi

  # Detect SSO-token-expired error strings. Scope the regex so we don't loop
  # `aws sso login` against a misconfigured profile (e.g. "sso-session not found
  # in config" is a config error, not an expiry).
  if echo "$sts_output" | grep -qiE '(Token has expired|ExpiredToken|SSO session[^.]*(expired|invalid|refresh)|ssoToken|Error loading SSO Token|refresh failed)'; then
    if [ "${AUTH_METHOD:-}" = "sso" ] && [ -n "${SSO_SESSION_NAME:-}" ]; then
      echo ""
      info "SSO session expired for profile '$AWS_PROFILE'. Launching browser login..."
      if aws sso login --sso-session "$SSO_SESSION_NAME"; then
        if aws sts get-caller-identity --profile "$AWS_PROFILE" --output text >/dev/null 2>&1; then
          local identity
          identity=$(aws sts get-caller-identity --profile "$AWS_PROFILE" --output text --query 'Arn' 2>/dev/null || echo "unknown")
          info "Authenticated as: $identity"
          return 0
        fi
      fi
    fi
  fi

  echo ""
  error "AWS credentials for profile '$AWS_PROFILE' are not valid."
  echo "Details: $sts_output"
  echo ""
  if [ "${AUTH_METHOD:-}" = "sso" ]; then
    echo "Try: aws sso login --sso-session ${SSO_SESSION_NAME:-<your-session>}"
  else
    echo "Try: aws configure --profile $AWS_PROFILE  (or re-run 'make setup')"
  fi
  exit 1
}

# -----------------------------------------------------------------------------
# Cross-Account Profile Helpers
# -----------------------------------------------------------------------------
# Per-target CLI profiles for accounts the agent needs to assume into. Reuse
# the primary $SSO_SESSION_NAME so a single browser login authenticates every
# sub-profile. Multi-IdC is out of scope.
#
# Discovery is data-driven from SSM: any populated cross_account/*_role_arn
# parameter becomes a candidate sub-profile. The mapping below is the single
# source of truth for which SSM keys are SSO-derivable.

# Emit the catalogue of SSO-derivable cross-account targets, one per line:
#   <profile_suffix>:<ssm_subkey>:<env_field_in_dotenv>:<human_label>
auth_sso_cross_account_targets() {
  cat <<'EOF'
ce:cross_account/default_role_arn:CE_SSO_ROLE_NAME:Cost Explorer / Network Resilience
coh:cross_account/coh_role_arn:COH_SSO_ROLE_NAME:Cost Optimization Hub
tag-gov:cross_account/tag_governance_role_arn:TAG_GOV_SSO_ROLE_NAME:Tag Governance
health:cross_account/health_role_arn:HEALTH_SSO_ROLE_NAME:Health Events
EOF
}

# Idempotent. Rewrites any existing [profile $profile_name] block.
setup_cross_account_sso_profile() {
  local profile_name="$1"    # e.g. cloudops-ce
  local session_name="$2"    # always the primary session ($SSO_SESSION_NAME)
  local account_id="$3"
  local role_name="$4"
  local region="$5"

  local aws_config="$HOME/.aws/config"
  mkdir -p "$(dirname "$aws_config")"
  touch "$aws_config"

  local tmp
  tmp=$(mktemp)
  awk -v p="[profile $profile_name]" '
    BEGIN { skip = 0 }
    /^\[/ { if ($0 == p) { skip = 1; next } else { skip = 0 } }
    skip == 0 { print }
  ' "$aws_config" > "$tmp"
  mv "$tmp" "$aws_config"

  cat >> "$aws_config" <<CONFIG

[profile $profile_name]
sso_session = $session_name
sso_account_id = $account_id
sso_role_name = $role_name
region = $region
output = json
CONFIG
}

# Parse the account-id out of a role ARN (arn:aws:iam::ACCT:role/ROLE).
# Echoes empty string if the ARN doesn't match.
auth_account_id_from_arn() {
  local arn="$1"
  echo "$arn" | sed -nE 's|^arn:[^:]*:iam::([0-9]+):role/.*$|\1|p'
}

# Configure one cross-account target during cmd_setup. Reads the role ARN
# straight from SSM (or skips if absent — first-run before `make configure`),
# extracts the account-id, prompts the developer for their permission set
# name, writes the [profile] block, and validates with sts.
#
# Inputs:
#   $1 = profile_suffix    (e.g. "ce")
#   $2 = ssm_subkey        (e.g. "cross_account/default_role_arn")
#   $3 = env_field         (e.g. "CE_SSO_ROLE_NAME" — read from the current
#                           shell as a default; user prompted if empty)
#   $4 = label             (human label, used in prompts)
#
# Exits 0 quietly when SSM has no value yet — the user can re-run setup after
# `make configure`.
configure_cross_account_target() {
  local suffix="$1"
  local ssm_subkey="$2"
  local env_field="$3"
  local label="$4"

  local arn
  arn="$(shared_config_get "$(echo "$ssm_subkey" | tr '[:lower:]/' '[:upper:]_')" "")"
  if [ -z "$arn" ]; then
    return 0
  fi

  local account_id
  account_id="$(auth_account_id_from_arn "$arn")"
  if [ -z "$account_id" ]; then
    warn "  ${label}: could not parse account-id from role ARN '${arn}' — skipping sub-profile."
    return 0
  fi

  local derived_profile="${PROJECT_PREFIX}-${suffix}"
  local existing_role="${!env_field:-}"

  local perm_set=""
  while [ -z "$perm_set" ]; do
    shared_config_prompt perm_set "  Permission set / role name in $account_id ($label)" "${existing_role:-}"
    [ -z "$perm_set" ] && echo "  Error: permission set name is required for SSO."
  done

  # Reuse existing profile if it's already pointing at the same account and
  # the session is live — no need to rewrite.
  local reuse="no"
  if aws configure list --profile "$derived_profile" >/dev/null 2>&1; then
    local existing_acct
    existing_acct=$(aws configure get sso_account_id --profile "$derived_profile" 2>/dev/null || echo "")
    if [ "$existing_acct" = "$account_id" ]; then
      if aws sts get-caller-identity --profile "$derived_profile" >/dev/null 2>&1; then
        echo "  Using existing SSO profile '$derived_profile' (session valid)."
        reuse="yes"
      fi
    fi
  fi

  if [ "$reuse" != "yes" ]; then
    echo "  Writing SSO profile '$derived_profile' (reusing session '$SSO_SESSION_NAME')..."
    setup_cross_account_sso_profile \
      "$derived_profile" \
      "$SSO_SESSION_NAME" \
      "$account_id" \
      "$perm_set" \
      "$AWS_REGION"

    if aws sts get-caller-identity --profile "$derived_profile" >/dev/null 2>&1; then
      echo "  Validated — account $account_id accessible via '$derived_profile'."
    else
      warn "  Could not access $account_id via permission set '$perm_set'."
      echo "    Check that your Identity Center admin has assigned this permission set in that account."
    fi
  fi

  # Persist the per-developer permission set choice for subsequent runs.
  save_env_var "$env_field" "$perm_set"
}

# Validate + auto-refresh all cross-account profiles before cross-account-only
# operations (e.g. `scripts/generate_cross_account_role_policies.sh`). Routine
# deploys do NOT need this — Lambdas use sts:AssumeRole at runtime.
preflight_cross_account() {
  local failed=0
  local refreshed=false
  local line suffix ssm_subkey env_field label profile

  while IFS=':' read -r suffix ssm_subkey env_field label; do
    [ -z "$suffix" ] && continue
    profile="${PROJECT_PREFIX}-${suffix}"
    aws configure list --profile "$profile" >/dev/null 2>&1 || continue

    if [ -n "${SSO_SESSION_NAME:-}" ]; then
      local profile_session
      profile_session=$(aws configure get sso_session --profile "$profile" 2>/dev/null || echo "")
      if [ -n "$profile_session" ] && [ "$profile_session" != "$SSO_SESSION_NAME" ]; then
        error "Profile '$profile' references sso_session='$profile_session'"
        echo "       but the project uses sso_session='$SSO_SESSION_NAME'."
        echo "       Fix: re-run 'make setup' to regenerate cross-account profiles."
        failed=1
        continue
      fi
    fi

    local sts_output
    if sts_output=$(aws sts get-caller-identity --profile "$profile" --output json 2>&1); then
      continue
    fi

    if echo "$sts_output" | grep -qiE '(Token has expired|ExpiredToken|SSO session[^.]*(expired|invalid|refresh)|ssoToken|Error loading SSO Token|refresh failed)'; then
      if [ "$refreshed" = false ] && [ -n "${SSO_SESSION_NAME:-}" ]; then
        info "Cross-account SSO session expired. Launching browser login for '$SSO_SESSION_NAME'..."
        if aws sso login --sso-session "$SSO_SESSION_NAME"; then
          refreshed=true
          sleep 3
        fi
      fi
      if aws sts get-caller-identity --profile "$profile" --output json >/dev/null 2>&1; then
        continue
      fi
    fi

    error "cross-account profile '$profile' is not valid."
    echo "Details: $sts_output"
    failed=1
  done < <(auth_sso_cross_account_targets)

  [ "$failed" -eq 0 ] || exit 1
}
