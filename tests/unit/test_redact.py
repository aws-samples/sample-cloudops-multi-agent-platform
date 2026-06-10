"""Tests for agents.shared.redact."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "agents"))

from agents.shared.redact import redact


def test_redacts_aws_account_id():
    assert redact("Account 123456789012 has costs") == "Account [REDACTED] has costs"


def test_redacts_iam_arn():
    text = "Role arn:aws:iam::123456789012:role/CloudOpsAgent-CostExplorerTool is used"
    result = redact(text)
    assert "arn:aws" not in result
    assert "[REDACTED]" in result


def test_redacts_access_key():
    akia_key = "AKIA" + "IOSFODNN7EXAMPLE"
    assert "[REDACTED]" in redact(f"Key is {akia_key}")
    temp_key = "ASIA" + "TESTKEY123456789"
    assert "[REDACTED]" in redact(f"Temp key {temp_key}")


def test_redacts_external_id():
    text = "ExternalId: my-secret-ext-id-12345"
    result = redact(text)
    assert "my-secret-ext-id" not in result


def test_redacts_role_session_name():
    text = "RoleSessionName=cloudops-deploy-session"
    result = redact(text)
    assert "cloudops-deploy-session" not in result


def test_preserves_normal_text():
    text = "Your AWS costs increased by 15% last month due to EC2 usage."
    assert redact(text) == text


def test_handles_empty_and_none():
    assert redact("") == ""
    assert redact(None) is None


def test_multiple_patterns_in_same_string():
    text = (
        "Lambda arn:aws:lambda:us-east-1:123456789012:function:CostTool "
        "assumed role with ExternalId=abc-123 in account 987654321098"
    )
    result = redact(text)
    assert "123456789012" not in result
    assert "987654321098" not in result
    assert "arn:aws" not in result
    assert "abc-123" not in result


def test_does_not_redact_short_numbers():
    text = "Found 42 resources costing $1500 per month"
    assert redact(text) == text
