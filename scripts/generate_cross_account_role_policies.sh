#!/usr/bin/env bash
# Generate CloudFormation templates that an admin deploys in a TARGET account
# to create IAM roles our Lambdas assume into. Permissions are pulled from
# src/lambda/mcp/tools.json so they stay in sync with the tool's own IAM
# policy (no manual duplication).
#
# One template per MCP tool whose tools.json declares a
# CROSS_ACCOUNT_ROLE_ARN* env var. Customer deploys the template in each
# target account individually (or via CloudFormation StackSets across an
# Organization for Scenario-2-style fan-out like network-resilience).
#
# Usage:
#   scripts/generate_cross_account_role_policies.sh           # emit into temp/
#   scripts/generate_cross_account_role_policies.sh --out DIR # override output dir
#
# Env vars consumed:
#   AWS_ACCOUNT_ID     — deployment account that will assume these roles
#                        (required; obtained via STS if unset)
#   PROJECT_PREFIX     — role-name prefix (defaults to "cloudops")
#
# The emitted templates are project-agnostic scaffolding the customer may
# customize. The script is deterministic (same inputs → same output), so
# regenerating and diffing is how you detect drift between the tool's own
# IAM policy (updated when tools.json changes) and the target-account role.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
OUT_DIR="${ROOT}/temp/cross-account-roles"

# -----------------------------------------------------------------------------
# Arg parsing
# -----------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)
      OUT_DIR="$2"
      shift 2
      ;;
    -h|--help)
      sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# //'
      exit 0
      ;;
    *)
      echo "Unknown flag: $1" >&2
      exit 2
      ;;
  esac
done

# -----------------------------------------------------------------------------
# Resolve deployment account ID + project prefix
# -----------------------------------------------------------------------------
if [[ -z "${AWS_ACCOUNT_ID:-}" ]]; then
  if ! AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)"; then
    echo "ERROR: AWS_ACCOUNT_ID not set and aws sts get-caller-identity failed." >&2
    echo "       Either export AWS_ACCOUNT_ID or run 'aws sso login' first." >&2
    exit 1
  fi
fi
PROJECT_PREFIX="${PROJECT_PREFIX:-cloudops}"

mkdir -p "${OUT_DIR}"

# -----------------------------------------------------------------------------
# Generate templates — one CFN file per cross-account-aware tool
# -----------------------------------------------------------------------------
"${ROOT}/.venv/bin/python" - <<PY
import json
import os
import sys
from pathlib import Path

root = Path("${ROOT}")
out = Path("${OUT_DIR}")
deploy_account = "${AWS_ACCOUNT_ID}"
prefix = "${PROJECT_PREFIX}"

tools_path = root / "src" / "lambda" / "mcp" / "tools.json"
tools = json.loads(tools_path.read_text())

generated = []

for tool_name, cfg in tools.items():
    env_vars = cfg.get("env_vars", {}) or {}
    xacct_keys = [k for k in env_vars if k.startswith("CROSS_ACCOUNT_ROLE_ARN")]
    if not xacct_keys:
        continue

    iam_actions = cfg.get("iam_actions", []) or []
    if not iam_actions:
        print(f"  [skip] {tool_name} declares CROSS_ACCOUNT_ROLE_ARN but has no iam_actions", file=sys.stderr)
        continue

    # Role name in the TARGET account. Customers can override at deploy time
    # via the RoleName parameter — the default encodes the tool + prefix.
    default_role_name = f"{prefix}-{tool_name}-xacct"

    # Trust the deployment account's Lambda execution role specifically,
    # not the account root. This is tighter ("confused deputy"-safe) and
    # lets the customer audit who can assume what.
    trusted_role_arn = (
        f"arn:aws:iam::{deploy_account}:role/{prefix}-{tool_name}-tool-role"
    )

    template = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": (
            f"Cross-account role for the CloudOps {tool_name} MCP Lambda. "
            "Deploy this stack in a target account (or as a StackSet across "
            "an Organization) to let the Lambda read data from this account."
        ),
        "Parameters": {
            "RoleName": {
                "Type": "String",
                "Default": default_role_name,
                "Description": "Name of the IAM role created in this account.",
            },
            "TrustedRoleArn": {
                "Type": "String",
                "Default": trusted_role_arn,
                "Description": (
                    "ARN of the Lambda execution role in the deployment "
                    "account that is allowed to assume this role."
                ),
            },
            "ExternalId": {
                "Type": "String",
                "Default": "",
                "Description": (
                    "Optional external ID for confused-deputy protection. "
                    "Leave empty to skip."
                ),
                "NoEcho": True,
            },
        },
        "Conditions": {
            "HasExternalId": {
                "Fn::Not": [{"Fn::Equals": [{"Ref": "ExternalId"}, ""]}]
            }
        },
        "Resources": {
            "CrossAccountRole": {
                "Type": "AWS::IAM::Role",
                "Properties": {
                    "RoleName": {"Ref": "RoleName"},
                    "MaxSessionDuration": 3600,
                    "AssumeRolePolicyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {"AWS": {"Ref": "TrustedRoleArn"}},
                                "Action": "sts:AssumeRole",
                                "Condition": {
                                    "Fn::If": [
                                        "HasExternalId",
                                        {
                                            "StringEquals": {
                                                "sts:ExternalId": {
                                                    "Ref": "ExternalId"
                                                }
                                            }
                                        },
                                        {"Ref": "AWS::NoValue"},
                                    ]
                                },
                            }
                        ],
                    },
                    "Policies": [
                        {
                            "PolicyName": f"{tool_name}-readonly",
                            "PolicyDocument": {
                                "Version": "2012-10-17",
                                "Statement": [
                                    {
                                        "Effect": "Allow",
                                        "Action": iam_actions,
                                        "Resource": "*",
                                    }
                                ],
                            },
                        }
                    ],
                    "Tags": [
                        {"Key": "project", "Value": prefix},
                        {"Key": "tool", "Value": tool_name},
                        {"Key": "managed-by", "Value": "cloudops-xacct-generator"},
                    ],
                },
            }
        },
        "Outputs": {
            "RoleArn": {
                "Description": (
                    f"ARN to wire into .env as CROSS_ACCOUNT_ROLE_ARN"
                    + ("_" + xacct_keys[0].removeprefix("CROSS_ACCOUNT_ROLE_ARN_")
                       if xacct_keys[0] != "CROSS_ACCOUNT_ROLE_ARN" else "")
                ),
                "Value": {"Fn::GetAtt": ["CrossAccountRole", "Arn"]},
                "Export": {"Name": {"Fn::Sub": "\${AWS::StackName}-RoleArn"}},
            }
        },
    }

    out_path = out / f"{tool_name}-cross-account-role.yaml"
    import yaml  # PyYAML is in the .venv; fall back to JSON if missing.
    try:
        out_path.write_text(yaml.safe_dump(template, sort_keys=False))
    except Exception:
        out_path = out / f"{tool_name}-cross-account-role.json"
        out_path.write_text(json.dumps(template, indent=2))

    env_hint = (
        f"CROSS_ACCOUNT_ROLE_ARN={'<RoleArn output>'}"
        if "CROSS_ACCOUNT_ROLE_ARN" in xacct_keys and
        not any(k.startswith("CROSS_ACCOUNT_ROLE_ARN_") for k in xacct_keys)
        else ", ".join(f"{k}=<RoleArn output>" for k in xacct_keys)
    )
    generated.append((tool_name, out_path, env_hint))

if not generated:
    print("No cross-account-aware tools found in tools.json — nothing to emit.")
    sys.exit(0)

print(f"Deployment account: {deploy_account}")
print(f"Wrote {len(generated)} CFN template(s) to {out}/:")
print()
for name, path, env_hint in generated:
    print(f"  {name:30s} -> {path.name}")
    print(f"    Deploy in the target account, then set: {env_hint}")
    print()
PY

# Stack-set hint lives in bash to avoid heredoc backslash escaping.
cat <<'EOF'
To deploy across an Organization (spoke-account fan-out pattern):
  aws cloudformation create-stack-set \
    --stack-set-name cloudops-xacct-<tool> \
    --template-body file://<template>.yaml \
    --capabilities CAPABILITY_NAMED_IAM \
    --permission-model SERVICE_MANAGED \
    --auto-deployment Enabled=true,RetainStacksOnAccountRemoval=false
EOF
