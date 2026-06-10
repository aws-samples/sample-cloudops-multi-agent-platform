#!/usr/bin/env bash
# Driver for cmd_setup with the SSO branch.
#
# Run from project root:
#   bash tests/scripts/test_cmd_setup_sso.sh
#
# Strategy: pipe canned answers into cmd_setup, stub `aws` so SSO login + sts
# always succeed, point HOME at a tempdir, point ENV_FILE at a tempfile, and
# inspect the resulting .env / ~/.aws/config to confirm the wizard wrote what
# we expect. The Python venv install step is skipped by setting a sentinel.

set -u

# -----------------------------------------------------------------------------
# Test plumbing
# -----------------------------------------------------------------------------
TESTS_RUN=0
TESTS_FAILED=0

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
    printf "  FAIL  %s\n        expected to contain: %q\n        in head:%s\n" \
      "$name" "$needle" "$(printf '%s' "$haystack" | head -20)"
  fi
}

assert_grep_count() {
  local file="$1" pattern="$2" expected="$3" name="$4"
  TESTS_RUN=$((TESTS_RUN + 1))
  local actual
  actual=$(grep -c "$pattern" "$file" 2>/dev/null || echo 0)
  actual=$(echo "$actual" | tr -d ' ')
  if [ "$actual" = "$expected" ]; then
    printf "  PASS  %s\n" "$name"
  else
    TESTS_FAILED=$((TESTS_FAILED + 1))
    printf "  FAIL  %s (expected %s match(es) for %q in %s, got %s)\n" \
      "$name" "$expected" "$pattern" "$file" "$actual"
  fi
}

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT"

# -----------------------------------------------------------------------------
# Run cmd_setup in a sandbox subshell with stubs in place.
# -----------------------------------------------------------------------------
run_setup() {
  local input="$1" extra_env_setup="$2"
  local sandbox; sandbox=$(mktemp -d)
  local home="$sandbox/home"
  local repo="$sandbox/repo"
  mkdir -p "$home" "$repo/scripts/lib" "$repo/scripts" "$repo/.venv/bin"

  # Mirror just the files cmd_setup needs (and a stub Makefile won't be touched).
  cp "$PROJECT_ROOT/scripts/lib/common.sh"        "$repo/scripts/lib/"
  cp "$PROJECT_ROOT/scripts/lib/shared_config.sh" "$repo/scripts/lib/"
  cp "$PROJECT_ROOT/scripts/lib/auth.sh"          "$repo/scripts/lib/"
  cp "$PROJECT_ROOT/scripts/lib/commands.sh"      "$repo/scripts/lib/"
  cp "$PROJECT_ROOT/scripts/shared-keys.txt"      "$repo/scripts/"
  # Real venv python — the modules call `.venv/bin/python` for JSON helpers.
  ln -sf "$PROJECT_ROOT/.venv/bin/python" "$repo/.venv/bin/python"
  # Empty pyproject so the pip install step inside the wizard has something
  # to hit when cmd_setup tries to pip install. We bypass that step by
  # creating .venv before cmd_setup runs (it skips creation when present).
  : > "$repo/pyproject.toml"

  # Stub aws on PATH.
  local stub_dir="$sandbox/bin"
  mkdir -p "$stub_dir"
  cat > "$stub_dir/aws" <<'STUB'
#!/usr/bin/env bash
case "$1:$2" in
  sts:get-caller-identity)
    echo '{"Arn":"arn:aws:sts::111111111111:assumed-role/StubAdmin/me","UserId":"AIDA","Account":"111111111111"}'
    exit 0 ;;
  sso:login)
    echo "Successfully logged into Start URL"
    exit 0 ;;
  ssm:get-parameters-by-path)
    echo '{"Parameters":[]}'
    exit 0 ;;
  configure:list)
    exit 0 ;;
  configure:get)
    echo "" ;;
  *)
    exit 0 ;;
esac
STUB
  chmod +x "$stub_dir/aws"

  # Stub pip so the pip install step is fast and offline.
  cat > "$stub_dir/pip" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
  chmod +x "$stub_dir/pip"
  cp "$stub_dir/pip" "$repo/.venv/bin/pip"

  # Drive cmd_setup
  HOME="$home" \
  PATH="$stub_dir:$PATH" \
  bash -c "
    set -u
    cd '$repo'
    export ENV_FILE='$repo/.env'
    export SCRIPT_DIR='$repo/scripts'
    $extra_env_setup
    source scripts/lib/common.sh
    source scripts/lib/shared_config.sh
    source scripts/lib/auth.sh
    source scripts/lib/commands.sh
    cmd_setup
  " <<< "$input"
  local rc=$?

  # Echo paths to caller via stdout (the test reads via SANDBOX env capture).
  echo "$sandbox"
  return $rc
}

echo
echo "--- Test 1: SSO branch from a clean .env ---"
INPUT_SSO=$(cat <<'EOF'
cloudops
dev
sso
https://acme.awsapps.com/start
us-east-1
111111111111
AdministratorAccess
us-east-1
EOF
)
SANDBOX=$(run_setup "$INPUT_SSO" "" 2>/dev/null | tail -1)
ENV_OUT=$(cat "$SANDBOX/repo/.env" 2>/dev/null || echo "")
AWS_CFG=$(cat "$SANDBOX/home/.aws/config" 2>/dev/null || echo "")

assert_contains "$ENV_OUT" "PROJECT_PREFIX=cloudops"      "writes PROJECT_PREFIX"
assert_contains "$ENV_OUT" "ENVIRONMENT=dev"              "writes ENVIRONMENT"
assert_contains "$ENV_OUT" "AUTH_METHOD=sso"              "writes AUTH_METHOD=sso"
assert_contains "$ENV_OUT" "AWS_PROFILE=cloudops-dev"     "derives AWS_PROFILE"
assert_contains "$ENV_OUT" "SSO_SESSION_NAME=cloudops-sso" "derives SSO_SESSION_NAME"
assert_contains "$ENV_OUT" "SSO_START_URL=https://acme.awsapps.com/start" "writes SSO_START_URL"
assert_contains "$ENV_OUT" "SSO_REGION=us-east-1"         "writes SSO_REGION"
assert_contains "$ENV_OUT" "SSO_ACCOUNT_ID=111111111111"  "writes SSO_ACCOUNT_ID"
assert_contains "$ENV_OUT" "SSO_ROLE_NAME=AdministratorAccess" "writes SSO_ROLE_NAME"

assert_contains "$AWS_CFG" "[sso-session cloudops-sso]"   "writes sso-session block"
assert_contains "$AWS_CFG" "[profile cloudops-dev]"       "writes primary [profile] block"
assert_contains "$AWS_CFG" "sso_account_id = 111111111111" "writes account id in profile"
rm -rf "$SANDBOX"

echo
echo "--- Test 2: idempotent re-run (sso → sso, same answers) ---"
SANDBOX=$(run_setup "$INPUT_SSO" "" 2>/dev/null | tail -1)
# Re-run cmd_setup against the same sandbox by replaying the same input.
# Since each run_setup builds a fresh sandbox we do this by running the wizard
# twice in one shell instead.
SANDBOX2=$(mktemp -d)
HOME2="$SANDBOX2/home"; mkdir -p "$HOME2"
REPO2="$SANDBOX2/repo"; mkdir -p "$REPO2/scripts/lib" "$REPO2/.venv/bin"
cp "$PROJECT_ROOT/scripts/lib"/{common,shared_config,auth,commands}.sh "$REPO2/scripts/lib/"
cp "$PROJECT_ROOT/scripts/shared-keys.txt" "$REPO2/scripts/"
ln -sf "$PROJECT_ROOT/.venv/bin/python" "$REPO2/.venv/bin/python"
: > "$REPO2/pyproject.toml"
STUB_DIR="$SANDBOX2/bin"; mkdir -p "$STUB_DIR"
cat > "$STUB_DIR/aws" <<'STUB'
#!/usr/bin/env bash
case "$1:$2" in
  sts:get-caller-identity) echo '{"Arn":"arn:aws:sts::111111111111:role/X/me"}'; exit 0 ;;
  sso:login)               exit 0 ;;
  ssm:get-parameters-by-path) echo '{"Parameters":[]}'; exit 0 ;;
  configure:list)          exit 0 ;;
  configure:get)           echo "" ;;
  *)                       exit 0 ;;
esac
STUB
chmod +x "$STUB_DIR/aws"
cat > "$STUB_DIR/pip" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
chmod +x "$STUB_DIR/pip"
cp "$STUB_DIR/pip" "$REPO2/.venv/bin/pip"

HOME="$HOME2" PATH="$STUB_DIR:$PATH" bash -c "
  set -u
  cd '$REPO2'
  export ENV_FILE='$REPO2/.env'
  export SCRIPT_DIR='$REPO2/scripts'
  source scripts/lib/common.sh
  source scripts/lib/shared_config.sh
  source scripts/lib/auth.sh
  source scripts/lib/commands.sh
  cmd_setup <<INPUT
cloudops
dev
sso
https://acme.awsapps.com/start
us-east-1
111111111111
AdministratorAccess
us-east-1
INPUT
  cmd_setup <<INPUT
cloudops
dev
sso
https://acme.awsapps.com/start
us-east-1
111111111111
AdministratorAccess
us-east-1
INPUT
" >/dev/null 2>&1

assert_grep_count "$REPO2/.env"         '^AUTH_METHOD=sso$'      "1" "rerun does not duplicate AUTH_METHOD line"
assert_grep_count "$REPO2/.env"         '^SSO_START_URL='        "1" "rerun does not duplicate SSO_START_URL line"
assert_grep_count "$HOME2/.aws/config" '^\[sso-session cloudops-sso\]$' "1" "rerun does not duplicate sso-session block"
assert_grep_count "$HOME2/.aws/config" '^\[profile cloudops-dev\]$'      "1" "rerun does not duplicate [profile] block"
rm -rf "$SANDBOX" "$SANDBOX2"

echo
echo "--- Test 3: credentials branch (skips SSO prompts) ---"
INPUT_CREDS=$(cat <<'EOF'
cloudops
prod
credentials
us-west-2
N
EOF
)
SANDBOX=$(run_setup "$INPUT_CREDS" "" 2>/dev/null | tail -1)
ENV_OUT=$(cat "$SANDBOX/repo/.env" 2>/dev/null || echo "")

assert_contains "$ENV_OUT" "AUTH_METHOD=credentials" "writes AUTH_METHOD=credentials"
assert_contains "$ENV_OUT" "AWS_PROFILE=cloudops-prod" "derives AWS_PROFILE for prod env"

# Test 3b: credentials branch must NOT have written SSO_START_URL.
TESTS_RUN=$((TESTS_RUN + 1))
if grep -q '^SSO_START_URL=' "$SANDBOX/repo/.env" 2>/dev/null; then
  TESTS_FAILED=$((TESTS_FAILED + 1))
  printf "  FAIL  credentials branch wrote SSO_START_URL anyway\n"
else
  printf "  PASS  credentials branch leaves SSO_* keys untouched\n"
fi
rm -rf "$SANDBOX"

echo
echo "--- Test 4: legacy .env with stale shared keys triggers strip ---"
SANDBOX=$(mktemp -d)
mkdir -p "$SANDBOX/home" "$SANDBOX/repo/scripts/lib" "$SANDBOX/repo/.venv/bin"
cp "$PROJECT_ROOT/scripts/lib"/{common,shared_config,auth,commands}.sh "$SANDBOX/repo/scripts/lib/"
cp "$PROJECT_ROOT/scripts/shared-keys.txt" "$SANDBOX/repo/scripts/"
ln -sf "$PROJECT_ROOT/.venv/bin/python" "$SANDBOX/repo/.venv/bin/python"
: > "$SANDBOX/repo/pyproject.toml"
cat > "$SANDBOX/repo/.env" <<EOF
PROJECT_PREFIX=cloudops
ENVIRONMENT=dev
AWS_REGION=eu-west-1
GATEWAY_AUTH=oauth
EOF
STUB_DIR="$SANDBOX/bin"; mkdir -p "$STUB_DIR"
cat > "$STUB_DIR/aws" <<'STUB'
#!/usr/bin/env bash
case "$1:$2" in
  sts:get-caller-identity) echo '{"Arn":"arn:aws:sts::111111111111:role/X/me"}' ;;
  sso:login) ;;
  ssm:get-parameters-by-path) echo '{"Parameters":[]}' ;;
  *) ;;
esac
exit 0
STUB
chmod +x "$STUB_DIR/aws"
cat > "$STUB_DIR/pip" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
chmod +x "$STUB_DIR/pip"
cp "$STUB_DIR/pip" "$SANDBOX/repo/.venv/bin/pip"

HOME="$SANDBOX/home" PATH="$STUB_DIR:$PATH" bash -c "
  set -u
  cd '$SANDBOX/repo'
  export ENV_FILE='$SANDBOX/repo/.env'
  export SCRIPT_DIR='$SANDBOX/repo/scripts'
  source scripts/lib/common.sh
  source scripts/lib/shared_config.sh
  source scripts/lib/auth.sh
  source scripts/lib/commands.sh
  cmd_setup <<INPUT
cloudops
dev
sso
https://acme.awsapps.com/start
us-east-1
111111111111
AdministratorAccess
us-east-1
Y
INPUT
" >/dev/null 2>&1

ENV_OUT=$(cat "$SANDBOX/repo/.env")
TESTS_RUN=$((TESTS_RUN + 1))
if grep -q '^AWS_REGION=' "$SANDBOX/repo/.env" || grep -q '^GATEWAY_AUTH=' "$SANDBOX/repo/.env"; then
  TESTS_FAILED=$((TESTS_FAILED + 1))
  printf "  FAIL  stale keys not stripped from .env\n"
else
  printf "  PASS  stale shared-config keys stripped from .env\n"
fi
rm -rf "$SANDBOX"

echo
echo "----------------------------------------"
echo "Ran $TESTS_RUN tests, $TESTS_FAILED failed."
echo "----------------------------------------"
exit "$TESTS_FAILED"
