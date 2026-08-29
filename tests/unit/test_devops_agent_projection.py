from __future__ import annotations

import json

from shared.devops_agent_projection import (
    project_investigation,
    project_mitigation,
)


def record(record_type, record_id, content, *, execution_id="exec-1", created_at=""):
    return {
        "recordType": record_type,
        "recordId": record_id,
        "executionId": execution_id,
        "createdAt": created_at or "2026-08-22T04:00:00Z",
        "content": json.dumps(content) if not isinstance(content, str) else content,
    }


def test_projects_clear_incident_and_root_cause_without_supporting_observations():
    records = [
        record(
            "investigation_summary",
            "structured",
            {
                "symptoms": [
                    {
                        "title": "API errors",
                        "description": "Requests returned HTTP 502.",
                        "start_time": "2026-08-22T03:00:00Z",
                        "end_time": "2026-08-22T03:05:00Z",
                        "supporting_observations": [{"secret": "verbose"}],
                    }
                ],
                "findings": [
                    {
                        "type": "root_cause",
                        "title": "Target unavailable",
                        "description": "The only target restarted during patching.",
                        "supporting_observations": [{"detail": "verbose"}],
                    }
                ],
            },
        ),
        record(
            "finding",
            "finding-1",
            {
                "id": "root",
                "title": "Target unavailable",
                "description": "The only target restarted during patching.",
                "supporting_observations": [{"detail": "verbose"}],
            },
        ),
    ]

    projection = project_investigation(records)

    assert projection["incident"] == {
        "title": "API errors",
        "description": "Requests returned HTTP 502.",
        "startedAt": "2026-08-22T03:00:00Z",
        "endedAt": "2026-08-22T03:05:00Z",
    }
    assert projection["rootCause"] == {
        "title": "Target unavailable",
        "description": "The only target restarted during patching.",
    }
    assert "supporting" not in json.dumps(projection).lower()


def test_projects_mitigation_from_exact_summary_id_and_execution():
    records = [
        record(
            "mitigation_summary",
            "structured",
            {
                "mitigation_summary": {
                    "action": "No infrastructure change required",
                    "reasoning": "The transient condition has already cleared.",
                }
            },
            execution_id="mitigation-exec",
        ),
        record(
            "mitigation_summary_md",
            "mitigation-summary-id",
            "# Mitigation Summary\n## Action\nNo infrastructure change required",
            execution_id="mitigation-exec",
        ),
    ]

    mitigation = project_mitigation(
        records,
        execution_id="mitigation-exec",
        summary_record_id="mitigation-summary-id",
        status="COMPLETED",
        updated_at="2026-08-22T04:05:00Z",
    )

    assert mitigation == {
        "status": "COMPLETED",
        "terminal": True,
        "action": "No infrastructure change required",
        "description": "The transient condition has already cleared.",
        "updatedAt": "2026-08-22T04:05:00Z",
    }
    assert (
        project_mitigation(
            records,
            execution_id="mitigation-exec",
            summary_record_id="different-summary-id",
            status="COMPLETED",
        )
        is None
    )


def test_activity_keeps_only_compact_milestones():
    records = [
        record("message", "message-1", {"content": "verbose"}),
        record(
            "symptom",
            "symptom-1",
            {
                "id": "symptom",
                "title": "Elevated errors",
                "description": "Errors exceeded the threshold.",
                "supporting_observations": [{"large": "payload"}],
            },
        ),
    ]

    assert project_investigation(records)["activity"] == [
        {
            "id": "symptom",
            "title": "Elevated errors",
            "createdAt": "2026-08-22T04:00:00Z",
            "details": "Errors exceeded the threshold.",
            "kind": "symptom",
        }
    ]
