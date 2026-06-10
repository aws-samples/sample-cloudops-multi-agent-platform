#!/usr/bin/env bash
# Unit tests for scripts/lib/auth.sh
#
# Run from project root:
#   bash tests/scripts/test_auth_sh.sh
#
# Strategy: source auth.sh + common.sh + shared_config.sh in a clean shell with
# HOME pointed at a tempdir so we can inspect the rewritten ~/.aws/config without
# touching the real one. AWS CLI calls are short-circuited via a stub on PATH.

set -u

# -----------------------------------------------------------------------------
# Test plumbing
# -----------------------------------------------------------------------------
TESTS_RUN=0
TESTS_FAILED=0
ASSERT_FAIL_DETAILS=()

assert_eq() {
  local actual="$1" expected="$2" name="$3"
  TESTS_RUN=$((TESTS_RUN + 1))
  if [ "$actual" = "$expected" ]; then
    printf "  PASS  %s\n" "$name"
  else
    TESTS_FAILED=$((TESTS_FAILED + 1))
    printf "  FAIL  %s\n        expected: %q\n        actual:   %q\n" \
      "$name" "$expected" "$actual"
  fi
}

assert_contains() {
  local haystack="$1" needle="$2" name="$3"
  TESTS_RUN=$((TESTS_RUN + 1))
  if printf '%s' "$haystack" | grep -qF -- "$needle"; then
    printf "  PASS  %s\n" "$name"
  else
    TESTS_FAILED=$((TESTS_FAILED + 1))
    printf "  FAIL  %s\n        expected to contain: %q\n        in: %q\n" \
      "$name" "$needle" "$haystack"
  fi
}

assert_not_contains() {
  local haystack="$1" needle="$2" name="$3"
  TESTS_RUN=$((TESTS_RUN + 1))
  if printf '%s' "$haystack" | grep -qF -- "$needle"; then
    TESTS_FAILED=$((TESTS_FAILED + 1))
    printf "  FAIL  %s\n        expected NOT to contain: %q\n        in: %q\n" \
      "$name" "$needle" "$haystack"
  else
    printf "  PASS  %s\n" "$name"
  fi
}

# -----------------------------------------------------------------------------
# Setup an isolated environment
# -----------------------------------------------------------------------------
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT"

TMPHOME="$(mktemp -d)"
trap 'rm -rf "$TMPHOME"' EXIT
export HOME="$TMPHOME"
export AWS_PROFILE="cloudops-dev"
export AWS_REGION="us-east-1"
export PROJECT_PREFIX="cloudops"
export ENVIRONMENT="dev"
export SSO_SESSION_NAME="cloudops-sso"
export AUTH_METHOD="sso"

# Stub aws CLI — controlled by $AWS_STUB_BEHAVIOR.
STUB_DIR="$TMPHOME/bin"
mkdir -p "$STUB_DIR"
cat > "$STUB_DIR/aws" <<'STUB'
#!/usr/bin/env bash
# Minimal aws CLI stub. Behavior controlled by AWS_STUB_BEHAVIOR:
#   ok         — get-caller-identity returns valid JSON
#   expired    — get-caller-identity prints SSO expired message to stderr, exit 255
#   missing    — get-caller-identity prints generic config error, exit 255
case "${AWS_STUB_BEHAVIOR:-ok}:$1:$2" in
  ok:sts:get-caller-identity)
    echo '{"Arn":"arn:aws:iam::111111111111:role/StubRole/me","UserId":"AIDA","Account":"111111111111"}'
    exit 0 ;;
  expired:sts:get-caller-identity)
    echo "Error loading SSO Token: Token has expired and refresh failed" >&2
    exit 255 ;;
  missing:sts:get-caller-identity)
    echo "Could not connect to the endpoint URL" >&2
    exit 255 ;;
  *:configure:list)
    exit 0 ;;
  *:configure:get)
    echo "" ;;
  *:sso:login)
    echo "Successfully logged into Start URL"
    exit 0 ;;
  *)
    exit 0 ;;
esac
STUB
chmod +x "$STUB_DIR/aws"
export PATH="$STUB_DIR:$PATH"

# Stub .venv/bin/python for preflight_auth's identity-extract helper.
# Real .venv exists in CI; for local-only test runs we ensure python3 is callable.
if [ ! -x "$PROJECT_ROOT/.venv/bin/python" ]; then
  mkdir -p "$STUB_DIR/.venv/bin"
  ln -sf "$(command -v python3)" "$STUB_DIR/.venv/bin/python" 2>/dev/null || true
fi

# Source the libs under test.
SCRIPT_DIR="$PROJECT_ROOT/scripts"
# shellcheck disable=SC1091
source "$PROJECT_ROOT/scripts/lib/common.sh"
# shellcheck disable=SC1091
source "$PROJECT_ROOT/scripts/lib/shared_config.sh"
# shellcheck disable=SC1091
source "$PROJECT_ROOT/scripts/lib/auth.sh"

# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------

echo
echo "auth_account_id_from_arn"
assert_eq "$(auth_account_id_from_arn 'arn:aws:iam::123456789012:role/CrossAccountReader')" \
  "123456789012" "parses standard role ARN"
assert_eq "$(auth_account_id_from_arn 'arn:aws-us-gov:iam::555555555555:role/Gov')" \
  "555555555555" "parses gov-cloud role ARN"
assert_eq "$(auth_account_id_from_arn 'not-an-arn')" "" "returns empty for malformed input"
assert_eq "$(auth_account_id_from_arn '')" "" "returns empty for empty input"

echo
echo "auth_sso_cross_account_targets"
TARGETS_OUT="$(auth_sso_cross_account_targets)"
TARGETS_LINES=$(printf '%s\n' "$TARGETS_OUT" | wc -l | tr -d ' ')
assert_eq "$TARGETS_LINES" "4" "emits 4 target lines"
assert_contains "$TARGETS_OUT" "ce:cross_account/default_role_arn:CE_SSO_ROLE_NAME:" "ce target present"
assert_contains "$TARGETS_OUT" "coh:cross_account/coh_role_arn:COH_SSO_ROLE_NAME:" "coh target present"
assert_contains "$TARGETS_OUT" "tag-gov:cross_account/tag_governance_role_arn:TAG_GOV_SSO_ROLE_NAME:" "tag-gov target present"
assert_contains "$TARGETS_OUT" "health:cross_account/health_role_arn:HEALTH_SSO_ROLE_NAME:" "health target present"

echo
echo "setup_sso_profile (idempotency)"
AWS_CFG="$HOME/.aws/config"
setup_sso_profile "cloudops-sso" "cloudops-dev" "https://example.awsapps.com/start" \
  "us-east-1" "111111111111" "AdminAccess" "us-east-1" >/dev/null
FIRST_RUN=$(cat "$AWS_CFG")
assert_contains "$FIRST_RUN" "[sso-session cloudops-sso]" "writes sso-session block"
assert_contains "$FIRST_RUN" "[profile cloudops-dev]" "writes profile block"
assert_contains "$FIRST_RUN" "sso_account_id = 111111111111" "writes account id"

# Re-run with different role name — old block should be replaced, not duplicated.
setup_sso_profile "cloudops-sso" "cloudops-dev" "https://example.awsapps.com/start" \
  "us-east-1" "111111111111" "ReadOnlyAccess" "us-east-1" >/dev/null
SECOND_RUN=$(cat "$AWS_CFG")
PROFILE_COUNT=$(grep -c '^\[profile cloudops-dev\]$' "$AWS_CFG" || true)
SESSION_COUNT=$(grep -c '^\[sso-session cloudops-sso\]$' "$AWS_CFG" || true)
assert_eq "$PROFILE_COUNT" "1" "rerun does not duplicate [profile] block"
assert_eq "$SESSION_COUNT" "1" "rerun does not duplicate [sso-session] block"
assert_contains "$SECOND_RUN" "sso_role_name = ReadOnlyAccess" "rerun updates role name"
assert_not_contains "$SECOND_RUN" "sso_role_name = AdminAccess" "rerun strips old role name"

echo
echo "setup_cross_account_sso_profile (rewrite-only, no session block)"
setup_cross_account_sso_profile "cloudops-ce" "cloudops-sso" "222222222222" "CEReader" "us-east-1" >/dev/null
XACCT_OUT=$(cat "$AWS_CFG")
assert_contains "$XACCT_OUT" "[profile cloudops-ce]" "cross-account profile block written"
assert_contains "$XACCT_OUT" "sso_session = cloudops-sso" "cross-account profile reuses primary session name"
# Ensure setup_cross_account_sso_profile did NOT add a duplicate [sso-session] block.
SESSION_COUNT_AFTER=$(grep -c '^\[sso-session cloudops-sso\]$' "$AWS_CFG" || true)
assert_eq "$SESSION_COUNT_AFTER" "1" "cross-account profile does not duplicate sso-session block"

echo
echo "preflight_auth — happy path"
unset AWS_STUB_BEHAVIOR
PREFLIGHT_OUT=$(AWS_STUB_BEHAVIOR=ok preflight_auth 2>&1)
PREFLIGHT_RC=$?
assert_eq "$PREFLIGHT_RC" "0" "returns 0 with valid creds"
assert_contains "$PREFLIGHT_OUT" "Authenticated as:" "prints identity"

echo
echo "preflight_auth — no profile is a clean no-op"
SAVED_PROFILE="$AWS_PROFILE"
unset AWS_PROFILE
PREFLIGHT_OUT=$(preflight_auth 2>&1)
PREFLIGHT_RC=$?
assert_eq "$PREFLIGHT_RC" "0" "no-ops when AWS_PROFILE unset"
assert_eq "$PREFLIGHT_OUT" "" "prints nothing when AWS_PROFILE unset"
export AWS_PROFILE="$SAVED_PROFILE"

echo
echo "preflight_auth — expired SSO token triggers login"
PREFLIGHT_OUT=$(AWS_STUB_BEHAVIOR=expired preflight_auth 2>&1 || true)
# After expired error, our stub's 'sso login' returns 0 but the next sts call
# is also our stub which respects AWS_STUB_BEHAVIOR=expired (still failing).
# The point is to verify the sso-login was attempted, not that it eventually succeeds.
assert_contains "$PREFLIGHT_OUT" "SSO session expired" "detects expiry and announces login"

echo
echo "preflight_auth — non-SSO error does not loop into aws sso login"
PREFLIGHT_OUT=$(AWS_STUB_BEHAVIOR=missing preflight_auth 2>&1 || true)
assert_not_contains "$PREFLIGHT_OUT" "SSO session expired" "config errors do not match expiry regex"
assert_contains "$PREFLIGHT_OUT" "are not valid" "reports invalid creds for non-SSO errors"

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo
echo "----------------------------------------"
echo "Ran $TESTS_RUN tests, $TESTS_FAILED failed."
echo "----------------------------------------"
exit "$TESTS_FAILED"
