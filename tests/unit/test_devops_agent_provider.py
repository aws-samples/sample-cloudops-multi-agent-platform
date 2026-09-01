from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import boto3

from shared.devops_agent_provider import DevOpsAgentProvider


class Pages:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **kwargs):
        return iter(self.pages)


def _client():
    client = boto3.client("devops-agent", region_name="us-east-1")
    client.create_backlog_task = MagicMock()
    client.get_backlog_task = MagicMock()
    client.update_backlog_task = MagicMock()
    client.get_paginator = MagicMock()
    return client


def _task(status="IN_PROGRESS"):
    return {
        "task": {
            "agentSpaceId": "space-1",
            "taskId": "task-1",
            "executionId": "exec-1",
            "title": "Health event",
            "taskType": "INVESTIGATION",
            "priority": "HIGH",
            "status": status,
            "createdAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "updatedAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "version": 1,
        }
    }


def test_create_uses_investigation_task_and_stable_token():
    client = _client()
    client.create_backlog_task.return_value = _task()
    provider = DevOpsAgentProvider("space-1", "us-east-1", client=client)
    request = {
        "investigationId": "inv-1",
        "title": "Health event",
        "description": "Investigate",
        "priority": "HIGH",
    }

    first = provider.create_investigation(request)
    provider.create_investigation(request)

    assert first.reference.task_id == "task-1"
    assert first.reference.execution_id == "exec-1"
    calls = client.create_backlog_task.call_args_list
    assert calls[0].kwargs["taskType"] == "INVESTIGATION"
    assert "reference" not in calls[0].kwargs
    assert calls[0].kwargs["clientToken"] == calls[1].kwargs["clientToken"]


def test_associations_are_paginated_and_only_valid_accounts_count():
    client = _client()
    client.get_paginator.return_value = Pages(
        [
            {
                "associations": [
                    {
                        "status": "valid",
                        "configuration": {"sourceAws": {"accountId": "111111111111"}},
                    },
                    {
                        "status": "pending-confirmation",
                        "configuration": {"sourceAws": {"accountId": "222222222222"}},
                    },
                ]
            },
            {
                "associations": [
                    {
                        "status": "valid",
                        "configuration": {"aws": {"accountId": "333333333333"}},
                    },
                    {
                        "status": "invalid",
                        "configuration": {"aws": {"accountId": "444444444444"}},
                    },
                ]
            },
        ]
    )
    provider = DevOpsAgentProvider("space-1", "us-east-1", client=client)

    assert provider.list_covered_accounts() == {
        "111111111111",
        "333333333333",
    }


def test_journal_records_are_paginated_without_shape_changes():
    client = _client()
    records = [
        {
            "agentSpaceId": "space-1",
            "executionId": "exec-1",
            "recordId": "r1",
            "content": {"markdown": "summary"},
            "createdAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "recordType": "investigation_summary_md",
        },
        {
            "agentSpaceId": "space-1",
            "executionId": "exec-1",
            "recordId": "r2",
            "content": {"step": "checked logs"},
            "createdAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "recordType": "analysis",
        },
    ]
    client.get_paginator.return_value = Pages(
        [{"records": [records[0]]}, {"records": [records[1]]}]
    )
    provider = DevOpsAgentProvider("space-1", "us-east-1", client=client)

    result = provider.list_journal_records("exec-1")

    assert [record["recordId"] for record in result] == ["r1", "r2"]
    assert result[0]["recordType"] == "investigation_summary_md"
    assert result[0]["createdAt"] == "2026-01-01T00:00:00+00:00"


def test_cancel_uses_api_canceled_spelling():
    client = _client()
    client.update_backlog_task.return_value = _task("CANCELED")
    provider = DevOpsAgentProvider("space-1", "us-east-1", client=client)

    result = provider.cancel("task-1")

    assert result.reference.status == "CANCELED"
    assert client.update_backlog_task.call_args.kwargs["taskStatus"] == "CANCELED"
