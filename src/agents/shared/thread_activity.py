"""Per-thread activity tracking for cross-tab/cross-navigation awareness.

When a user navigates away from a thread that's still running an
invocation (chat or report), the frontend currently sees a blank screen.
This module persists a single "activity row" per thread to the
``report_templates`` DynamoDB table so the frontend can poll and show a
"still working on X" card plus disable the composer.

Storage schema (re-uses ``report_templates`` table):

    userId     = ``thread-activity:{actor_id}``
    templateId = ``{thread_id}``
    status     = ``running`` | ``idle`` | ``error``
    currentStep (S)  — human-readable current action ("Querying Cost Explorer")
    startedAt   (S)  — ISO timestamp the invocation began
    updatedAt   (S)  — ISO timestamp of the most recent step update
    runId       (S)  — AG-UI run id (optional)
    reportId    (S)  — report_id if the activity is a report (optional)
    errorMsg    (S)  — present only when status=error

Usage:

    from agents.shared.thread_activity import (
        mark_thread_running,
        update_thread_step,
        mark_thread_idle,
        mark_thread_error,
    )

    mark_thread_running(thread_id, actor_id, "Processing your request…", run_id)
    try:
        ...
        update_thread_step(thread_id, actor_id, "Calling cost-explorer")
        ...
    except Exception as exc:
        mark_thread_error(thread_id, actor_id, str(exc))
        raise
    finally:
        mark_thread_idle(thread_id, actor_id)

``get_thread_activity`` is read-only and used by the frontend Lambda.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import boto3

logger = logging.getLogger(__name__)

_STALE_MINUTES = 10  # updatedAt older than this → frontend treats as idle

_ddb_client = None


def _get_ddb(region: str):
    global _ddb_client
    if _ddb_client is None:
        _ddb_client = boto3.client("dynamodb", region_name=region)
    return _ddb_client


def _table() -> str:
    return os.environ.get("REPORT_TABLE_NAME", "")


def _region() -> str:
    return os.environ.get("AWS_REGION", "us-east-1")


def _pk(actor_id: str) -> str:
    return f"thread-activity:{actor_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mark_thread_running(
    thread_id: str,
    actor_id: str,
    step: str,
    run_id: str = "",
    report_id: Optional[str] = None,
) -> None:
    """Upsert the activity row with status=running."""
    table = _table()
    if not table or not thread_id or not actor_id:
        return
    item: dict = {
        "userId": {"S": _pk(actor_id)},
        "templateId": {"S": thread_id},
        "status": {"S": "running"},
        "currentStep": {"S": step},
        "startedAt": {"S": _now()},
        "updatedAt": {"S": _now()},
    }
    if run_id:
        item["runId"] = {"S": run_id}
    if report_id:
        item["reportId"] = {"S": report_id}
    try:
        _get_ddb(_region()).put_item(TableName=table, Item=item)
    except Exception as exc:
        logger.warning("mark_thread_running failed: %s", exc)


def update_thread_step(thread_id: str, actor_id: str, step: str) -> None:
    """Bump the current step. No-op if the row is already idle."""
    table = _table()
    if not table or not thread_id or not actor_id or not step:
        return
    try:
        _get_ddb(_region()).update_item(
            TableName=table,
            Key={"userId": {"S": _pk(actor_id)}, "templateId": {"S": thread_id}},
            UpdateExpression="SET currentStep = :s, updatedAt = :u",
            ConditionExpression="#st = :running",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":s": {"S": step},
                ":u": {"S": _now()},
                ":running": {"S": "running"},
            },
        )
    except _get_ddb(_region()).exceptions.ConditionalCheckFailedException:
        # Row is idle or missing — don't resurrect it.
        return
    except Exception as exc:
        logger.warning("update_thread_step failed: %s", exc)


def mark_thread_idle(thread_id: str, actor_id: str) -> None:
    """Mark the thread idle. Overwrites currentStep with empty."""
    table = _table()
    if not table or not thread_id or not actor_id:
        return
    try:
        _get_ddb(_region()).update_item(
            TableName=table,
            Key={"userId": {"S": _pk(actor_id)}, "templateId": {"S": thread_id}},
            UpdateExpression="SET #st = :idle, currentStep = :empty, updatedAt = :u",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":idle": {"S": "idle"},
                ":empty": {"S": ""},
                ":u": {"S": _now()},
            },
        )
    except Exception as exc:
        logger.warning("mark_thread_idle failed: %s", exc)


def mark_thread_error(thread_id: str, actor_id: str, error_msg: str) -> None:
    table = _table()
    if not table or not thread_id or not actor_id:
        return
    try:
        _get_ddb(_region()).update_item(
            TableName=table,
            Key={"userId": {"S": _pk(actor_id)}, "templateId": {"S": thread_id}},
            UpdateExpression="SET #st = :err, errorMsg = :e, updatedAt = :u",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":err": {"S": "error"},
                ":e": {"S": error_msg[:500]},
                ":u": {"S": _now()},
            },
        )
    except Exception as exc:
        logger.warning("mark_thread_error failed: %s", exc)


def get_thread_activity(
    thread_id: str, actor_id: str, region: str = ""
) -> dict:
    """Read an activity row; returns {'status': 'idle'} when absent.

    Used by the frontend Lambda, not by the runtime itself, but kept
    alongside the writer helpers so the schema is documented in one
    place. Stale ``running`` rows (updatedAt older than _STALE_MINUTES)
    are reported as ``idle`` — this is how the frontend recovers if a
    runtime dies without reaching its ``finally`` block.
    """
    table = _table()
    reg = region or _region()
    if not table or not thread_id or not actor_id:
        return {"status": "idle"}
    try:
        resp = _get_ddb(reg).get_item(
            TableName=table,
            Key={"userId": {"S": _pk(actor_id)}, "templateId": {"S": thread_id}},
        )
    except Exception as exc:
        logger.warning("get_thread_activity failed: %s", exc)
        return {"status": "idle"}

    item = resp.get("Item")
    if not item:
        return {"status": "idle"}

    status = item.get("status", {}).get("S", "idle")
    updated_at = item.get("updatedAt", {}).get("S", "")

    if status == "running" and _is_stale(updated_at):
        return {"status": "idle"}

    return {
        "status": status,
        "current_step": item.get("currentStep", {}).get("S", ""),
        "started_at": item.get("startedAt", {}).get("S", ""),
        "updated_at": updated_at,
        "run_id": item.get("runId", {}).get("S", ""),
        "report_id": item.get("reportId", {}).get("S", ""),
        "error_msg": item.get("errorMsg", {}).get("S", ""),
    }


def _is_stale(iso_ts: str) -> bool:
    if not iso_ts:
        return True
    try:
        ts = datetime.fromisoformat(iso_ts)
    except ValueError:
        return True
    now = datetime.now(timezone.utc)
    return (now - ts).total_seconds() > _STALE_MINUTES * 60
