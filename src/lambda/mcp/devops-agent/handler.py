"""AgentCore Gateway operations for AWS DevOps Agent investigations."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import boto3

from shared.devops_agent_coordinator import build_coordinator
from shared.devops_agent_redaction import redact as _redact

HEALTH_TABLE_NAME = os.environ.get("HEALTH_EVENTS_TABLE_NAME", "")
_coordinator = None
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_ACCOUNT_ID_RE = re.compile(r"^\d{12}$")
_HEALTH_EVENT_ARN_RE = re.compile(
    r"^arn:(?:aws|aws-us-gov|aws-cn):health:[a-z0-9-]+::event/"
    r"[A-Za-z0-9._/-]{1,2000}$"
)
_PRIORITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL"}


def _text(event: dict[str, Any], name: str, limit: int) -> str:
    value = event.get(name)
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if 0 < len(value) <= limit else ""


def _get_coordinator():
    global _coordinator
    if _coordinator is None:
        _coordinator = build_coordinator()
    return _coordinator


def _health_row(event_arn: str, account_id: str) -> dict[str, Any] | None:
    if not HEALTH_TABLE_NAME:
        raise RuntimeError("HEALTH_EVENTS_TABLE_NAME not configured")
    table = boto3.resource("dynamodb").Table(HEALTH_TABLE_NAME)
    return table.get_item(Key={"eventArn": event_arn, "accountId": account_id}).get(
        "Item"
    )


def investigate_health_event(event: dict[str, Any]) -> dict[str, Any]:
    event_arn = _text(event, "event_arn", 2048)
    account_id = _text(event, "account_id", 12)
    if not event_arn or not account_id:
        return {"error": "event_arn and account_id are required"}
    if not _HEALTH_EVENT_ARN_RE.fullmatch(event_arn) or not _ACCOUNT_ID_RE.fullmatch(
        account_id
    ):
        return {"error": "invalid_health_event_reference"}
    if os.environ.get("DEVOPS_AGENT_INTEGRATION_ENABLED", "false").lower() != "true":
        return {"error": "devops_agent_integration_disabled"}
    row = _health_row(event_arn, account_id)
    if not row:
        return {"found": False, "error": "health_event_not_found"}
    result = _get_coordinator().request_health(row, automatic=False, force_recheck=True)
    if result is None:
        return {"error": "devops_agent_integration_disabled"}
    return _redact({"found": True, "investigation": result})


def get_health_investigation(event: dict[str, Any]) -> dict[str, Any]:
    event_arn = _text(event, "event_arn", 2048)
    account_id = _text(event, "account_id", 12)
    if not event_arn or not account_id:
        return {"error": "event_arn and account_id are required"}
    if not _HEALTH_EVENT_ARN_RE.fullmatch(event_arn) or not _ACCOUNT_ID_RE.fullmatch(
        account_id
    ):
        return {"error": "invalid_health_event_reference"}
    response = _get_coordinator().get_health(
        event_arn,
        account_id,
        include_journal=bool(
            event.get("include_activity", event.get("include_journal", False))
        ),
    )
    return _redact(response)


def investigate_operational_issue(event: dict[str, Any]) -> dict[str, Any]:
    required = ("account_id", "source_id", "title", "description")
    missing = [name for name in required if not str(event.get(name, "")).strip()]
    if missing:
        return {"error": f"{', '.join(missing)} are required"}
    if os.environ.get("DEVOPS_AGENT_INTEGRATION_ENABLED", "false").lower() != "true":
        return {"error": "devops_agent_integration_disabled"}

    account_id = _text(event, "account_id", 12)
    source_id = _text(event, "source_id", 1000)
    title = _text(event, "title", 400)
    description = _text(event, "description", 10000)
    priority = str(event.get("priority", "HIGH")).upper()
    if (
        not _ACCOUNT_ID_RE.fullmatch(account_id)
        or not source_id
        or not title
        or not description
        or priority not in _PRIORITIES
    ):
        return {"error": "invalid_operational_issue"}

    result = _get_coordinator().request_operational_issue(
        account_id=account_id,
        source_id=source_id,
        title=title,
        description=description,
        priority=priority,
        force_recheck=True,
    )
    if result is None:
        return {"error": "devops_agent_integration_disabled"}
    return _redact({"found": True, "investigation": result})


def handler(event, context):
    request_id = str(getattr(context, "aws_request_id", ""))
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    extended_name = str(custom.get("bedrockAgentCoreToolName", ""))
    if not extended_name:
        logger.warning(
            json.dumps(
                {
                    "operation": "unknown",
                    "requestId": request_id,
                    "error": "missing_tool_name",
                }
            )
        )
        return {"error": "invalid_tool_invocation"}
    tool_name = extended_name.split("___", 1)[-1]
    handlers = {
        "investigate_health_event": investigate_health_event,
        "investigate_operational_issue": investigate_operational_issue,
        "get_health_investigation": get_health_investigation,
    }
    fn = handlers.get(tool_name)
    if not fn:
        return {"error": "unknown_tool"}
    if not isinstance(event, dict):
        return {"error": "invalid_request"}
    logger.info(json.dumps({"operation": tool_name, "requestId": request_id}))
    try:
        return fn(event)
    except Exception:
        logger.exception(
            "DevOps Agent tool failed",
            extra={"operation": tool_name, "requestId": request_id},
        )
        response = {"error": "devops_agent_request_failed"}
        if request_id:
            response["requestId"] = request_id
        return response
