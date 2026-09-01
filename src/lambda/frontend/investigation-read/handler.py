"""Browser API for persisted DevOps Agent investigations and explicit refresh."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from decimal import Decimal
from typing import Any

import boto3

from shared.devops_agent_redaction import redact

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE_NAME = os.environ.get("INVESTIGATIONS_TABLE_NAME", "")
AGENT_SPACE_ID = os.environ.get("DEVOPS_AGENT_SPACE_ID", "")
RECONCILE_FUNCTION_ARN = os.environ.get("DEVOPS_AGENT_RECONCILE_FUNCTION_ARN", "")
_table = None
_lambda = None

_TERMINAL_STATES = {"COMPLETED", "FAILED"}


def _get_table():
    global _table
    if _table is None:
        if not TABLE_NAME:
            raise RuntimeError("INVESTIGATIONS_TABLE_NAME not configured")
        _table = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _table


def _get_lambda():
    global _lambda
    if _lambda is None:
        if not RECONCILE_FUNCTION_ARN:
            raise RuntimeError("DEVOPS_AGENT_RECONCILE_FUNCTION_ARN not configured")
        _lambda = boto3.client("lambda")
    return _lambda


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _response(status_code: int, body: dict[str, Any] | None = None, **headers) -> dict:
    response_headers = {
        "Content-Type": "application/json",
        "Cache-Control": "private, no-cache",
        **headers,
    }
    return {
        "statusCode": status_code,
        "headers": response_headers,
        "body": "" if body is None else json.dumps(body, default=_json_default),
    }


def _revision(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _provider_url(meta: dict[str, Any]) -> str | None:
    task_id = str(meta.get("providerTaskId", ""))
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", task_id):
        return None
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", AGENT_SPACE_ID):
        return None
    return f"https://{AGENT_SPACE_ID}.aidevops.global.app.aws/investigation/{task_id}"


def handler(event, context):
    investigation_id = str(
        (event.get("pathParameters") or {}).get("investigationId", "")
    )
    if not investigation_id:
        return _response(400, {"error": "investigationId is required"})

    method = str(
        (event.get("requestContext") or {}).get("http", {}).get("method", "GET")
    )
    if method == "POST":
        response = _get_lambda().invoke(
            FunctionName=RECONCILE_FUNCTION_ARN,
            InvocationType="RequestResponse",
            Payload=json.dumps({"investigationId": investigation_id}).encode("utf-8"),
        )
        payload = response.get("Payload")
        if payload is not None:
            payload.read()
        if response.get("FunctionError"):
            logger.error(
                "DevOps Agent refresh invocation failed",
                extra={"investigationId": investigation_id},
            )
            return _response(502, {"error": "investigation_refresh_failed"})

    table = _get_table()
    meta = table.get_item(Key={"PK": f"INV#{investigation_id}", "SK": "META"}).get(
        "Item"
    )
    if not meta:
        return _response(404, {"error": "investigation_not_found"})

    workflow_state = str(meta.get("workflowState", ""))
    outcome = meta.get("outcome")
    outcome = outcome if isinstance(outcome, dict) else {}
    payload = {
        "investigation": {
            "investigationId": str(meta.get("investigationId", investigation_id)),
            "title": str(meta.get("requestTitle", "AWS Health investigation")),
            "workflowState": workflow_state,
            "providerStatus": str(meta.get("providerStatus", "")),
            "reason": meta.get("reason"),
            "createdAt": meta.get("createdAt"),
            "updatedAt": meta.get("updatedAt"),
            "completedAt": meta.get("completedAt"),
            "terminal": workflow_state in _TERMINAL_STATES,
            "providerUrl": _provider_url(meta),
        },
        "outcome": {
            "incident": outcome.get("incident"),
            "rootCause": outcome.get("rootCause"),
            "mitigation": outcome.get("mitigation"),
        },
        "activity": meta.get("activity", []),
    }
    revision = _revision(payload)
    etag = f'"{revision}"'
    request_headers = {
        str(key).lower(): value for key, value in (event.get("headers") or {}).items()
    }
    if request_headers.get("if-none-match") == etag:
        return _response(304, ETag=etag)

    payload["revision"] = revision
    return _response(200, redact(payload), ETag=etag)
