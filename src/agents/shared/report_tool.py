"""Per-request `get_report` Strands tool for frontend agents.

The supervisor (and any promoted-to-frontend leaf/mid agent) needs to
answer follow-up questions about reports it has already generated —
"break down the top service from section 2 of the FinOps report",
"compare against last month's tag review", "edit recommendation 3".

The full rendered report body lives in DynamoDB
(``userId="report:{actor_id}", templateId="{report_id}"``). The agent's
``messages`` history (rebuilt from AgentCore Memory on each turn) only
contains a tiny ``<report-pending report_id="..." title="..."/>`` marker
per generated report — the body is deliberately NOT duplicated into
Memory because reports can exceed AgentCore's 100k-char per-event limit.

This module provides ``make_get_report_tool`` — a factory that closes
over the requesting actor's ``actor_id`` so a tool call is constrained
to that actor's reports. The model passes a ``report_id`` (which it
extracts from a marker in its visible history); the tool reads the row
from DynamoDB and returns trimmed sections.

Isolation guarantees (composed, not enforced by this module alone):

1. Memory is session-scoped: ``list_events`` returns only this session's
   events, so the markers visible in ``messages`` are only from this
   session. The model has no other source of valid ``report_id`` values.
2. DDB partition key is ``"report:{actor_id}"`` where ``actor_id`` is
   captured from the request payload at tool-construction time. Even if
   the user pastes another actor's ``report_id`` into chat, the lookup
   uses *this* requester's actor_id and returns ``not found``.
3. Fabricated ``report_id`` values resolve to ``not found`` because the
   templateId space is UUID-suffixed.

The tool result is the source of truth for the model's follow-up
answer — section content, status, version, parent_report_id.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from strands import tool

from agents.shared.registry import get_current_handler

logger = logging.getLogger(__name__)

REPORT_TABLE_NAME = os.environ.get("REPORT_TABLE_NAME", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _parse_section(s: dict) -> dict:
    """Convert a DDB section map to a plain dict the model can read."""
    m = s.get("M", {}) if isinstance(s, dict) else {}
    traces_str = m.get("traces", {}).get("S", "")
    try:
        traces = json.loads(traces_str) if traces_str else []
    except (json.JSONDecodeError, TypeError):
        traces = []
    return {
        "id": m.get("id", {}).get("S", ""),
        "title": m.get("title", {}).get("S", ""),
        "status": m.get("status", {}).get("S", ""),
        "content": m.get("content", {}).get("S", ""),
        "error": m.get("error", {}).get("S", ""),
        "traces": traces,
    }


def _parse_report_item(item: dict) -> dict:
    """Convert a full DDB report item to a plain dict.

    Each section carries its own ``traces`` list — tool calls (input,
    output, sub-call hierarchy, status, duration) recorded during that
    section's generation. The model can use these to answer follow-ups
    about HOW the report was built, not just WHAT it concluded.
    """
    return {
        "report_id": item.get("templateId", {}).get("S", ""),
        "title": item.get("title", {}).get("S", ""),
        "status": item.get("status", {}).get("S", ""),
        "month": item.get("month", {}).get("S", ""),
        "year": item.get("year", {}).get("S", ""),
        "version": int(item.get("version", {}).get("N", "1")),
        "parent_report_id": item.get("parentReportId", {}).get("S", ""),
        "edit_prompt": item.get("editPrompt", {}).get("S", ""),
        "current_section": int(item.get("currentSection", {}).get("N", "0")),
        "total_sections": int(item.get("totalSections", {}).get("N", "0")),
        "sections": [
            _parse_section(s) for s in item.get("sections", {}).get("L", [])
        ],
    }


def make_get_report_tool(
    actor_id: str,
    session_id: str = "",
    region: str = "",
    table_name: str = "",
):
    """Build a per-request ``get_report`` Strands tool bound to ``actor_id``.

    The actor_id is captured at construction time, NOT taken from the
    model's argument. This is the security boundary: the model can pass
    any ``report_id`` it likes, but the lookup is always scoped to the
    actor of the current request.

    Args:
        actor_id: The requesting user's sanitized identifier
            (e.g. ``"alice_at_example_com"``).
        session_id: The current chat session id, for audit logging only —
            not used in the DDB key.
        region: AWS region. Defaults to the ``AWS_REGION`` env var.
        table_name: DynamoDB table holding reports. Defaults to the
            ``REPORT_TABLE_NAME`` env var (set on the supervisor runtime
            and on every promoted-to-frontend agent runtime).

    Returns:
        A Strands ``@tool``-decorated function the agent can call. If
        ``actor_id`` or ``table_name`` is missing, returns a stub tool
        that always errors — the supervisor will surface the error to
        the user instead of fabricating an answer.
    """
    region = region or AWS_REGION
    table_name = table_name or REPORT_TABLE_NAME

    @tool(
        name="get_report",
        description=(
            "Read a previously generated report from storage. Use this when "
            "the user references a report in this conversation — by title "
            '("the FinOps report"), by topic ("the cost report", "the tag '
            'review"), by section ("section 2 of the health events report"), '
            'or by recency ("the latest report", "the one I just generated"). '
            "The `report_id` to pass is on the corresponding "
            '`<report-pending report_id="..."/>` marker in your message history. '
            "Returns the report's title, status, all sections (id, title, "
            "content as markdown), version, and parent_report_id (for edits). "
            "If the user references a report you cannot find in your message "
            "history, do NOT guess a report_id — answer that you don't see "
            "that report in the conversation."
        ),
    )
    def _get_report(report_id: str) -> dict:
        """Fetch a report by ID, scoped to the requesting actor.

        Args:
            report_id: The ``report_id`` extracted from a ``<report-pending>``
                marker in the agent's ``messages`` history.

        Returns:
            On success: dict with ``report_id``, ``title``, ``status``,
            ``month``, ``year``, ``version``, ``parent_report_id``,
            ``sections`` (list of ``{id, title, status, content}``).
            On failure: ``{"error": "..."}``.
        """
        if not actor_id:
            logger.warning(
                "get_report called without actor_id (session=%s, report_id=%s)",
                session_id[:20] if session_id else "",
                report_id,
            )
            return {
                "error": (
                    "Cannot read reports: no actor identity on this request."
                )
            }
        if not table_name:
            logger.error(
                "get_report called without REPORT_TABLE_NAME (actor=%s, report_id=%s)",
                actor_id,
                report_id,
            )
            return {
                "error": (
                    "Cannot read reports: report storage is not configured "
                    "on this runtime."
                )
            }
        if not report_id or not isinstance(report_id, str):
            return {"error": "report_id is required"}

        handler = get_current_handler()
        try:
            ddb = boto3.client("dynamodb", region_name=region)
            resp = ddb.get_item(
                TableName=table_name,
                Key={
                    "userId": {"S": f"report:{actor_id}"},
                    "templateId": {"S": report_id},
                },
            )
        except Exception as exc:
            logger.error(
                "get_report DDB error (actor=%s, report_id=%s): %s",
                actor_id,
                report_id,
                exc,
            )
            if handler:
                handler.fail_tool("get_report", str(exc))
            return {"error": f"failed to read report: {exc}"}

        item = resp.get("Item")
        if not item:
            # Audit log: actor requested a report_id that doesn't exist
            # under their partition. This is normal for fabricated /
            # mistyped ids; clusters of these from a single actor would
            # signal a cross-session leak attempt.
            logger.info(
                "get_report not_found actor=%s session=%s report_id=%s",
                actor_id,
                session_id[:20] if session_id else "",
                report_id,
            )
            result = {
                "error": (
                    f"Report '{report_id}' not found. It may have been "
                    "deleted, or the id may be from a different conversation."
                )
            }
            if handler:
                handler.complete_tool(
                    tool_name="get_report",
                    output=json.dumps(result),
                    input_data={"report_id": report_id},
                )
            return result

        parsed = _parse_report_item(item)
        logger.info(
            "get_report ok actor=%s session=%s report_id=%s status=%s sections=%d",
            actor_id,
            session_id[:20] if session_id else "",
            report_id,
            parsed.get("status"),
            len(parsed.get("sections", [])),
        )
        if handler:
            # Trimmed output for the trace card — the full sections dict
            # would balloon the trace UI. The agent itself still receives
            # the full parsed dict as the tool result.
            trace_output = json.dumps(
                {
                    "report_id": parsed["report_id"],
                    "title": parsed["title"],
                    "status": parsed["status"],
                    "section_count": len(parsed["sections"]),
                    "version": parsed["version"],
                }
            )
            handler.complete_tool(
                tool_name="get_report",
                output=trace_output,
                input_data={"report_id": report_id},
            )
        return parsed

    return _get_report
