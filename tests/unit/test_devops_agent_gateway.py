from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "src" / "lambda" / "mcp" / "devops-agent" / "handler.py"
SPEC = importlib.util.spec_from_file_location("devops_gateway_handler", PATH)
gateway = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gateway)


def test_recursive_redaction_preserves_structure_and_identifiers():
    value = {
        "accountId": "123456789012",
        "resourceArn": "arn:aws:lambda:us-east-1:123456789012:function:test",
        "nested": {
            "password": "do-not-return",
            "message": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        },
        "items": [{"accessKeyId": "example-access-key"}],
    }

    result = gateway._redact(value)

    assert result["accountId"] == "123456789012"
    assert result["resourceArn"].startswith("arn:aws:")
    assert result["nested"]["password"] == "[REDACTED]"
    assert "Bearer" not in result["nested"]["message"]
    assert result["items"][0]["accessKeyId"] == "[REDACTED]"


def test_redaction_handles_json_encoded_journal_content():
    content = (
        '{"finding":"healthy","details":{"sessionToken":"secret",'
        '"authorization":"Bearer abcdefghijklmnopqrstuvwxyz"}}'
    )

    result = gateway._redact(content)
    decoded = gateway.json.loads(result)

    assert decoded["finding"] == "healthy"
    assert decoded["details"]["sessionToken"] == "[REDACTED]"
    assert decoded["details"]["authorization"] == "[REDACTED]"


def test_operational_issue_validates_required_fields():
    assert gateway.investigate_operational_issue({}) == {
        "error": "account_id, source_id, title, description are required"
    }


def test_operational_issue_dispatches_to_coordinator(monkeypatch):
    class Coordinator:
        def __init__(self):
            self.request = None

        def request_operational_issue(self, **kwargs):
            self.request = kwargs
            return {
                "investigationId": "inv-alarm",
                "requestTitle": kwargs["title"],
            }

    coordinator = Coordinator()
    monkeypatch.setenv("DEVOPS_AGENT_INTEGRATION_ENABLED", "true")
    monkeypatch.setattr(gateway, "_get_coordinator", lambda: coordinator)

    result = gateway.investigate_operational_issue(
        {
            "account_id": "111111111111",
            "source_id": "alarm-arn#transition-time",
            "title": "Alarm: HighErrors",
            "description": "Alarm is in ALARM.",
            "priority": "CRITICAL",
        }
    )

    assert result["found"] is True
    assert result["investigation"]["investigationId"] == "inv-alarm"
    assert coordinator.request == {
        "account_id": "111111111111",
        "source_id": "alarm-arn#transition-time",
        "title": "Alarm: HighErrors",
        "description": "Alarm is in ALARM.",
        "priority": "CRITICAL",
        "force_recheck": True,
    }


def test_operational_issue_rejects_invalid_account_id(monkeypatch):
    monkeypatch.setenv("DEVOPS_AGENT_INTEGRATION_ENABLED", "true")

    result = gateway.investigate_operational_issue(
        {
            "account_id": "not-an-account",
            "source_id": "alarm#transition",
            "title": "Alarm",
            "description": "Sensitive incident details",
        }
    )

    assert result == {"error": "invalid_operational_issue"}


def test_health_investigation_rejects_non_health_arn(monkeypatch):
    monkeypatch.setenv("DEVOPS_AGENT_INTEGRATION_ENABLED", "true")

    result = gateway.investigate_health_event(
        {
            "account_id": "111111111111",
            "event_arn": "arn:aws:s3:::not-a-health-event",
        }
    )

    assert result == {"error": "invalid_health_event_reference"}


def test_handler_logs_no_request_payload_and_returns_stable_error(monkeypatch, caplog):
    class ClientContext:
        custom = {"bedrockAgentCoreToolName": "gateway___investigate_operational_issue"}

    class Context:
        aws_request_id = "request-1"
        client_context = ClientContext()

    def fail(_event):
        raise RuntimeError("provider secret detail")

    monkeypatch.setattr(gateway, "investigate_operational_issue", fail)
    caplog.set_level("INFO")

    result = gateway.handler(
        {"description": "sensitive incident payload"},
        Context(),
    )

    assert result == {
        "error": "devops_agent_request_failed",
        "requestId": "request-1",
    }
    assert "sensitive incident payload" not in caplog.text
    assert "provider secret detail" in caplog.text
