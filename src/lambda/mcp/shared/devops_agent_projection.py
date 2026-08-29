"""Compact, presentation-safe projections of AWS DevOps Agent journals."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

MILESTONE_TYPES = {"symptom", "finding", "investigation_gap"}
MITIGATION_TERMINAL_STATUSES = {
    "COMPLETED",
    "FAILED",
    "TIMED_OUT",
    "CANCELED",
}


def decode_content(content: Any) -> Any:
    current = content
    for _ in range(3):
        if not isinstance(current, str):
            break
        try:
            current = json.loads(current)
        except (TypeError, ValueError):
            break
    return current


def _text(value: Any, limit: int = 4000) -> str:
    return str(value).strip()[:limit] if isinstance(value, str) else ""


def _record_content(record: dict[str, Any]) -> dict[str, Any]:
    decoded = decode_content(record.get("content"))
    return decoded if isinstance(decoded, dict) else {}


def _fallback_title(record_type: str, content: dict[str, Any]) -> str:
    if record_type == "symptom":
        return "Symptom identified"
    if record_type == "investigation_gap":
        return "Investigation gap identified"
    if (
        content.get("type") == "root_cause"
        or content.get("finding_type") == "root_cause"
    ):
        return "Root cause identified"
    if content.get("resolution") == "ruled_out":
        return "Hypothesis ruled out"
    return "Finding recorded"


def project_activity(
    records: list[dict[str, Any]], limit: int = 8
) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        record_type = str(record.get("recordType", ""))
        if record_type not in MILESTONE_TYPES:
            continue
        content = _record_content(record)
        title = _text(content.get("title"), 180) or _fallback_title(
            record_type, content
        )
        details = "\n\n".join(
            part
            for part in (
                _text(content.get("description") or content.get("analysis"), 1000),
                _text(content.get("resolution_reason"), 1000),
            )
            if part
        )[:1200]
        identity = _text(content.get("id"), 255) or (
            f"{record_type}:{title.casefold()}"
        )
        latest[identity] = {
            "id": identity,
            "title": title,
            "createdAt": _text(record.get("createdAt"), 100),
            "details": details,
            "kind": (
                "symptom"
                if record_type == "symptom"
                else "gap" if record_type == "investigation_gap" else "finding"
            ),
        }

    priority = {"symptom": 0, "finding": 1, "gap": 2}
    selected = sorted(
        latest.values(),
        key=lambda item: (priority[item["kind"]], item["createdAt"]),
    )[:limit]
    return sorted(selected, key=lambda item: item["createdAt"])


def _summary_content(records: list[dict[str, Any]], record_type: str) -> dict[str, Any]:
    for record in reversed(records):
        if record.get("recordType") == record_type:
            content = _record_content(record)
            if content:
                return content
    return {}


def _incident_from_summary(summary: dict[str, Any]) -> dict[str, Any] | None:
    symptoms = summary.get("symptoms")
    if not isinstance(symptoms, list):
        return None
    symptom = next((item for item in symptoms if isinstance(item, dict)), None)
    if not symptom:
        return None
    return {
        "title": _text(symptom.get("title"), 240) or "Incident detected",
        "description": _text(symptom.get("description"), 3000),
        "startedAt": _text(symptom.get("start_time"), 100) or None,
        "endedAt": _text(symptom.get("end_time"), 100) or None,
    }


def _root_cause_from_summary(
    summary: dict[str, Any],
) -> dict[str, Any] | None:
    findings = summary.get("findings")
    if not isinstance(findings, list):
        return None
    candidates = [item for item in findings if isinstance(item, dict)]
    root_cause = next(
        (
            finding
            for finding in candidates
            if finding.get("type") == "root_cause"
            or finding.get("finding_type") == "root_cause"
        ),
        None,
    )
    cause = root_cause or next(
        (finding for finding in candidates if finding.get("type") == "cause"),
        None,
    )
    if not cause:
        return None
    return {
        "title": _text(cause.get("title"), 240) or "Root cause identified",
        "description": _text(
            cause.get("description") or cause.get("resolution_reason"), 3000
        ),
    }


def _fallback_incident(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    for record in records:
        if record.get("recordType") != "symptom":
            continue
        content = _record_content(record)
        return {
            "title": _text(content.get("title"), 240) or "Incident detected",
            "description": _text(content.get("description"), 3000),
            "startedAt": _text(content.get("start_time"), 100)
            or _text(record.get("createdAt"), 100)
            or None,
            "endedAt": _text(content.get("end_time"), 100) or None,
        }
    return None


def _fallback_root_cause(
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    findings = []
    for record in records:
        if record.get("recordType") != "finding":
            continue
        content = _record_content(record)
        if content:
            findings.append(content)
    cause = next(
        (
            item
            for item in findings
            if item.get("type") == "root_cause"
            or item.get("finding_type") == "root_cause"
        ),
        None,
    ) or next((item for item in findings if item.get("type") == "cause"), None)
    if not cause:
        return None
    return {
        "title": _text(cause.get("title"), 240) or "Root cause identified",
        "description": _text(
            cause.get("description") or cause.get("resolution_reason"), 3000
        ),
    }


def project_investigation(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = _summary_content(records, "investigation_summary")
    return {
        "incident": _incident_from_summary(summary) or _fallback_incident(records),
        "rootCause": _root_cause_from_summary(summary) or _fallback_root_cause(records),
        "activity": project_activity(records),
    }


def _markdown_section(markdown: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$([\s\S]*?)(?=^##\s+|\Z)",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(markdown)
    if not match:
        return ""
    lines = [
        re.sub(r"^[-*]\s+", "", line.strip())
        for line in match.group(1).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return _text(" ".join(lines), 3000)


def project_mitigation(
    records: list[dict[str, Any]],
    *,
    execution_id: str,
    summary_record_id: str,
    status: str,
    updated_at: str | None = None,
) -> dict[str, Any] | None:
    normalized_status = "CANCELED" if status == "CANCELLED" else status
    summary_record = next(
        (
            record
            for record in records
            if record.get("recordId") == summary_record_id
            and record.get("recordType") == "mitigation_summary_md"
            and (
                not execution_id
                or not record.get("executionId")
                or record.get("executionId") == execution_id
            )
        ),
        None,
    )
    if normalized_status == "COMPLETED" and not summary_record:
        return None

    structured = next(
        (
            _record_content(record)
            for record in reversed(records)
            if record.get("recordType") == "mitigation_summary"
            and (
                not execution_id
                or not record.get("executionId")
                or record.get("executionId") == execution_id
            )
        ),
        {},
    )
    summary = structured.get("mitigation_summary")
    summary = summary if isinstance(summary, dict) else {}
    action = _text(summary.get("action"), 1000)
    description = _text(summary.get("reasoning"), 3000)

    if summary_record and (not action or not description):
        decoded = decode_content(summary_record.get("content"))
        markdown = decoded if isinstance(decoded, str) else ""
        action = action or _markdown_section(markdown, "Action")
        description = description or _markdown_section(markdown, "Reasoning")

    return {
        "status": normalized_status,
        "terminal": normalized_status in MITIGATION_TERMINAL_STATUSES,
        "action": action,
        "description": description,
        "updatedAt": updated_at
        or _text((summary_record or {}).get("createdAt"), 100)
        or None,
    }


def merge_outcome(
    current: dict[str, Any] | None,
    *,
    incident: dict[str, Any] | None = None,
    root_cause: dict[str, Any] | None = None,
    mitigation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    outcome = deepcopy(current) if isinstance(current, dict) else {}
    if incident is not None:
        outcome["incident"] = incident
    if root_cause is not None:
        outcome["rootCause"] = root_cause
    if mitigation is not None:
        outcome["mitigation"] = mitigation
    return outcome
