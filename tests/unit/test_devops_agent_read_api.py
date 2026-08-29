from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "src" / "lambda" / "frontend" / "investigation-read" / "handler.py"
SPEC = importlib.util.spec_from_file_location("investigation_read_handler", PATH)
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)
api.TABLE_NAME = "investigations"
api.AGENT_SPACE_ID = "space-1"
api.RECONCILE_FUNCTION_ARN = "refresh-function"


class Table:
    def __init__(self, meta=None):
        self.item = meta

    def get_item(self, Key):
        assert Key["SK"] == "META"
        return {"Item": self.item} if self.item else {}


def event(investigation_id="inv-1", headers=None, method="GET"):
    return {
        "pathParameters": {"investigationId": investigation_id},
        "headers": headers or {},
        "requestContext": {"http": {"method": method}},
    }


def meta(**overrides):
    value = {
        "PK": "INV#inv-1",
        "SK": "META",
        "investigationId": "inv-1",
        "requestTitle": "AWS Health: EC2 issue",
        "workflowState": "RUNNING",
        "providerStatus": "IN_PROGRESS",
        "createdAt": "2026-01-01T00:00:00+00:00",
        "updatedAt": "2026-01-01T00:01:00+00:00",
        "providerTaskId": "task-1",
        "outcome": {
            "incident": {
                "title": "API errors",
                "description": "Requests returned HTTP 502.",
            },
            "rootCause": {
                "title": "Target unavailable",
                "description": "The target restarted.",
            },
        },
        "activity": [
            {
                "id": "finding-1",
                "title": "Target unavailable",
                "createdAt": "2026-01-01T00:01:00+00:00",
                "details": "The target restarted.",
                "kind": "finding",
            }
        ],
    }
    value.update(overrides)
    return value


def test_read_projects_safe_compact_fields(monkeypatch):
    table = Table(meta())
    monkeypatch.setattr(api, "_table", table)

    response = api.handler(event(), None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert response["headers"]["ETag"].startswith('"')
    assert body["investigation"]["terminal"] is False
    assert body["investigation"]["providerUrl"] == (
        "https://space-1.aidevops.global.app.aws/investigation/task-1"
    )
    assert body["outcome"]["incident"]["title"] == "API errors"
    assert body["activity"][0]["id"] == "finding-1"


def test_etag_returns_304_without_body(monkeypatch):
    monkeypatch.setattr(api, "_table", Table(meta()))
    first = api.handler(event(), None)

    response = api.handler(
        event(headers={"If-None-Match": first["headers"]["ETag"]}),
        None,
    )

    assert response["statusCode"] == 304
    assert response["body"] == ""
    assert response["headers"]["ETag"] == first["headers"]["ETag"]


def test_unknown_investigation_returns_404(monkeypatch):
    monkeypatch.setattr(api, "_table", Table())

    response = api.handler(event("missing"), None)

    assert response["statusCode"] == 404


def test_completed_response_discloses_structured_outcome(monkeypatch):
    table = Table(
        meta(
            workflowState="COMPLETED",
            providerStatus="COMPLETED",
            completedAt="2026-01-01T00:05:00+00:00",
            outcome={
                "incident": {"title": "API errors", "description": "HTTP 502."},
                "rootCause": {
                    "title": "Target unavailable",
                    "description": "The target restarted.",
                },
                "mitigation": {
                    "status": "COMPLETED",
                    "terminal": True,
                    "action": "No change required",
                    "description": "The condition cleared.",
                },
            },
        )
    )
    monkeypatch.setattr(api, "_table", table)

    body = json.loads(api.handler(event(), None)["body"])

    assert body["investigation"]["terminal"] is True
    assert body["outcome"]["mitigation"]["action"] == "No change required"


def test_post_refresh_invokes_reconciler_before_reading(monkeypatch):
    table = Table(meta(workflowState="COMPLETED", providerStatus="COMPLETED"))

    class Lambda:
        def invoke(self, **kwargs):
            assert kwargs["FunctionName"] == "refresh-function"
            assert kwargs["InvocationType"] == "RequestResponse"
            assert json.loads(kwargs["Payload"]) == {"investigationId": "inv-1"}
            table.item["outcome"]["mitigation"] = {
                "status": "COMPLETED",
                "terminal": True,
                "action": "Update the launch template",
                "description": "The referenced security group was deleted.",
            }
            return {}

    monkeypatch.setattr(api, "_table", table)
    monkeypatch.setattr(api, "_lambda", Lambda())

    response = api.handler(event(method="POST"), None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["outcome"]["mitigation"]["action"] == "Update the launch template"
