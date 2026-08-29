from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

import shared.devops_agent_coordinator as coordinator_module
from shared.devops_agent_coordinator import (
    COMPLETED,
    FAILED,
    FETCHING_RESULTS,
    MITIGATION_FETCHING,
    PENDING_ACCOUNT_ASSOCIATION,
    PENDING_CONCURRENCY_LIMIT,
    CoordinatorConfig,
    InvestigationCoordinator,
    InvestigationStore,
    journal_revision,
    source_hash,
)
from shared.devops_agent_provider import ProviderTask, TaskReference


class MemoryStore:
    def __init__(self, lease_failure=None):
        self.metas = {}
        self.pointers = {}
        self.tasks = {}
        self.lease_failure = lease_failure
        self.mitigation_work = {}
        self.events = set()
        self.lease_requests = []
        self.due_items = []
        self.due_states = ()

    def get_pointer(self, source):
        return self.pointers.get(source)

    def get_meta(self, investigation_id):
        item = self.metas.get(investigation_id)
        return deepcopy(item) if item else None

    def claim_source(self, meta, pointer):
        source = pointer["sourceKey"]
        if source in self.pointers:
            return False
        self.metas[meta["investigationId"]] = deepcopy(meta)
        self.pointers[source] = deepcopy(pointer)
        return True

    def update_meta(self, investigation_id, values, remove=()):
        self.metas[investigation_id].update(deepcopy(values))
        for key in remove:
            self.metas[investigation_id].pop(key, None)
        task_id = self.metas[investigation_id].get("providerTaskId")
        if task_id:
            self.tasks[task_id] = investigation_id
        return self.get_meta(investigation_id)

    def acquire_lease(self, investigation_id, agent_space_id, **kwargs):
        self.lease_requests.append(deepcopy(kwargs))
        if self.lease_failure:
            return self.lease_failure
        self.metas[investigation_id].update(
            {"leaseHeld": True, "workflowState": "CREATING"}
        )
        if kwargs.get("consume_daily_budget"):
            self.metas[investigation_id]["automaticBudgetConsumed"] = True
        return None

    def transition_and_release(
        self, investigation_id, agent_space_id, values, keep_due=False
    ):
        self.metas[investigation_id].update(values)
        self.metas[investigation_id]["leaseHeld"] = False
        if not keep_due:
            self.metas[investigation_id].pop("nextReconcileAt", None)

    def update_pointer(self, source, investigation_id):
        self.pointers[source]["investigationId"] = investigation_id

    def put_mitigation_work(self, item, expected=None):
        current = self.mitigation_work.get(item["SK"])
        if expected is not None and (
            not current
            or current.get("mitigationSummaryRecordId")
            != expected.get("mitigationSummaryRecordId")
            or current.get("mitigationUpdatedAt") != expected.get("mitigationUpdatedAt")
        ):
            return None
        self.mitigation_work[item["SK"]] = deepcopy(item)
        return deepcopy(item)

    def get_mitigation_work(self, investigation_id, execution_id):
        item = self.mitigation_work.get(f"MITIGATION#{execution_id}")
        return deepcopy(item) if item else None

    def delete_mitigation_work(self, item):
        current = self.mitigation_work.get(item["SK"])
        if (
            current
            and current.get("mitigationSummaryRecordId")
            == item.get("mitigationSummaryRecordId")
            and current.get("mitigationUpdatedAt") == item.get("mitigationUpdatedAt")
        ):
            self.mitigation_work.pop(item["SK"], None)

    def find_by_task(self, task_id):
        investigation_id = self.tasks.get(task_id)
        return self.get_meta(investigation_id) if investigation_id else None

    def mark_event(self, event_id, ttl):
        if event_id in self.events:
            return False
        self.events.add(event_id)
        return True

    def unmark_event(self, event_id):
        self.events.discard(event_id)

    def due(self, now, limit=100, states=()):
        self.due_states = states
        items = [
            *self.due_items,
            *self.mitigation_work.values(),
        ]
        return deepcopy(items[:limit])


class Provider:
    def __init__(self, covered=("111111111111",), status="IN_PROGRESS"):
        self.covered = set(covered)
        self.status = status
        self.create_calls = 0
        self.records = []
        self.records_by_execution = {}
        self.journal_calls = 0
        self.journal_error = None

    def list_covered_accounts(self, force_refresh=False):
        return frozenset(self.covered)

    def create_investigation(self, request):
        self.create_calls += 1
        return ProviderTask(TaskReference("task-1", "exec-1", self.status))

    def get_task(self, task_id):
        return ProviderTask(TaskReference(task_id, "exec-1", self.status))

    def list_journal_records(self, execution_id):
        self.journal_calls += 1
        if self.journal_error:
            raise self.journal_error
        return deepcopy(self.records_by_execution.get(execution_id, self.records))

    def cancel(self, task_id):
        self.status = "CANCELED"
        return self.get_task(task_id)


def config(**kwargs):
    values = {
        "agent_space_id": "space-1",
        "integration_enabled": True,
        "automatic_health_enabled": True,
    }
    values.update(kwargs)
    return CoordinatorConfig(**values)


def row(**kwargs):
    values = {
        "eventArn": "arn:aws:health:us-east-1::event/EC2/TEST/1",
        "accountId": "111111111111",
        "service": "EC2",
        "eventTypeCode": "TEST",
        "eventTypeCategory": "scheduledChange",
        "statusCode": "open",
        "actionability": "ACTION_REQUIRED",
        "riskLevel": "HIGH",
        "description": "test",
    }
    values.update(kwargs)
    return values


def test_concurrent_duplicate_source_creates_one_provider_task():
    store = MemoryStore()
    provider = Provider()
    coordinator = InvestigationCoordinator(store, provider, config())

    first = coordinator.request_health(row(), automatic=True)
    second = coordinator.request_health(row(), automatic=True)

    assert first["investigationId"] == second["investigationId"]
    assert provider.create_calls == 1
    assert len(store.pointers) == 1


def test_operational_issue_creates_and_reuses_one_provider_task():
    store = MemoryStore()
    provider = Provider()
    coordinator = InvestigationCoordinator(store, provider, config())
    request = {
        "account_id": "111111111111",
        "source_id": (
            "arn:aws:cloudwatch:us-east-1:111111111111:"
            "alarm:HighErrors#2026-08-22T01:02:03Z"
        ),
        "title": "Alarm: HighErrors",
        "description": "Alarm is in ALARM because API errors exceeded threshold.",
    }

    first = coordinator.request_operational_issue(**request)
    second = coordinator.request_operational_issue(**request)

    assert first["investigationId"] == second["investigationId"]
    assert first["source"] == "OPERATIONAL_ISSUE"
    assert first["automatic"] is False
    assert provider.create_calls == 1


def test_operational_issue_transition_creates_new_investigation():
    store = MemoryStore()
    provider = Provider()
    coordinator = InvestigationCoordinator(store, provider, config())
    common = {
        "account_id": "111111111111",
        "title": "Alarm: HighErrors",
        "description": "Alarm is in ALARM.",
    }

    first = coordinator.request_operational_issue(
        **common, source_id="alarm-arn#2026-08-22T01:02:03Z"
    )
    second = coordinator.request_operational_issue(
        **common, source_id="alarm-arn#2026-08-22T02:03:04Z"
    )

    assert first["investigationId"] != second["investigationId"]
    assert provider.create_calls == 2


def test_operational_issue_still_requires_account_association():
    store = MemoryStore()
    coordinator = InvestigationCoordinator(store, Provider(covered=()), config())

    result = coordinator.request_operational_issue(
        account_id="111111111111",
        source_id="alarm-arn#transition-time",
        title="Alarm: HighErrors",
        description="Alarm is in ALARM.",
    )

    assert result["workflowState"] == PENDING_ACCOUNT_ASSOCIATION
    assert result["reason"] == "SOURCE_ACCOUNT_NOT_ASSOCIATED"


def test_uncovered_account_is_deferred_and_claim_retained():
    store = MemoryStore()
    coordinator = InvestigationCoordinator(store, Provider(covered=()), config())

    result = coordinator.request_health(row(), automatic=True)

    assert result["workflowState"] == PENDING_ACCOUNT_ASSOCIATION
    assert result["reason"] == "SOURCE_ACCOUNT_NOT_ASSOCIATED"
    assert next(iter(store.pointers.values()))["investigationId"] == result["investigationId"]


def test_capacity_limit_defers_without_second_create():
    store = MemoryStore(lease_failure="capacity")
    provider = Provider()
    coordinator = InvestigationCoordinator(store, provider, config())

    result = coordinator.request_health(row(), automatic=True)

    assert result["workflowState"] == PENDING_CONCURRENCY_LIMIT
    assert provider.create_calls == 0


def test_open_issue_is_eligible_without_actionability_label():
    store = MemoryStore()
    provider = Provider()
    coordinator = InvestigationCoordinator(store, provider, config())

    result = coordinator.request_health(
        row(eventTypeCategory="issue", actionability=""), automatic=True
    )

    assert result["providerTaskId"] == "task-1"
    assert provider.create_calls == 1


def test_closed_issue_remains_manual_only():
    store = MemoryStore()
    provider = Provider()
    coordinator = InvestigationCoordinator(store, provider, config())

    result = coordinator.request_health(
        row(
            eventTypeCategory="issue",
            actionability="",
            statusCode="closed",
        ),
        automatic=True,
    )

    assert result is None
    assert provider.create_calls == 0


def test_daily_budget_is_consumed_only_on_first_lease():
    store = MemoryStore()
    provider = Provider()
    coordinator = InvestigationCoordinator(store, provider, config())

    first = coordinator.request_health(row(), automatic=True)
    store.transition_and_release(
        first["investigationId"],
        "space-1",
        {
            "workflowState": "PENDING_RATE_LIMIT",
            "nextReconcileAt": "2026-01-01T00:00:00+00:00",
        },
        keep_due=True,
    )
    coordinator.reconcile(store.get_meta(first["investigationId"]))

    assert store.lease_requests[0]["consume_daily_budget"] is True
    assert store.lease_requests[1]["consume_daily_budget"] is False


def test_completed_task_waits_for_summary_then_persists_compact_projection():
    store = MemoryStore()
    provider = Provider(status="COMPLETED")
    coordinator = InvestigationCoordinator(store, provider, config())
    provider.records = [
        {
            "agentSpaceId": "space-1",
            "executionId": "exec-1",
            "recordId": "summary-1",
            "content": {"markdown": "root cause"},
            "createdAt": "2026-01-01T00:00:00+00:00",
            "recordType": "investigation_summary_md",
        }
    ]

    result = coordinator.request_health(row(), automatic=True)

    assert result["workflowState"] == COMPLETED
    assert result["outcome"] == {}
    assert result["activity"] == []
    assert "summary" not in result
    assert store.get_meta(result["investigationId"])["leaseHeld"] is False


def test_completed_task_does_not_accept_non_summary_journals():
    store = MemoryStore()
    provider = Provider(status="COMPLETED")
    provider.records = [
        {
            "recordId": "analysis-1",
            "recordType": "analysis",
            "createdAt": "2026-01-01T00:00:00+00:00",
        }
    ]
    coordinator = InvestigationCoordinator(store, provider, config())

    result = coordinator.request_health(row(), automatic=True)

    assert result["workflowState"] == FETCHING_RESULTS
    assert not result.get("summaryRecordId")


def test_completed_task_selects_latest_summary_without_event_record_id():
    store = MemoryStore()
    provider = Provider(status="COMPLETED")
    provider.records = [
        {
            "executionId": "exec-1",
            "recordId": "summary-old",
            "recordType": "investigation_summary_md",
            "createdAt": "2026-01-01T00:00:00+00:00",
        },
        {
            "executionId": "exec-1",
            "recordId": "summary-new",
            "recordType": "investigation_summary_md",
            "createdAt": "2026-01-01T00:05:00+00:00",
        },
    ]
    coordinator = InvestigationCoordinator(store, provider, config())

    result = coordinator.request_health(row(), automatic=True)

    assert result["workflowState"] == COMPLETED
    assert result["summaryRecordId"] == "summary-new"


def test_completed_task_projects_mitigation_without_eventbridge_event():
    store = MemoryStore()
    provider = Provider(status="COMPLETED")
    provider.records = [
        {
            "executionId": "exec-1",
            "recordId": "investigation-summary",
            "recordType": "investigation_summary_md",
            "createdAt": "2026-01-01T00:05:00+00:00",
        },
        {
            "executionId": "exec-1",
            "recordId": "mitigation-structured",
            "recordType": "mitigation_summary",
            "createdAt": "2026-01-01T00:06:00+00:00",
            "content": {
                "mitigation_summary": {
                    "action": "Replace the deleted security group",
                    "reasoning": "The launch template references a missing group.",
                }
            },
        },
        {
            "executionId": "exec-1",
            "recordId": "mitigation-summary",
            "recordType": "mitigation_summary_md",
            "createdAt": "2026-01-01T00:06:00+00:00",
            "content": "# Mitigation Summary",
        },
    ]
    coordinator = InvestigationCoordinator(store, provider, config())

    result = coordinator.request_health(row(), automatic=True)

    assert result["workflowState"] == COMPLETED
    assert result["mitigationSummaryRecordId"] == "mitigation-summary"
    assert result["outcome"]["mitigation"]["action"] == (
        "Replace the deleted security group"
    )


def test_refresh_backfills_mitigation_for_completed_investigation():
    store = MemoryStore()
    provider = Provider(status="COMPLETED")
    provider.records = [
        {
            "executionId": "exec-1",
            "recordId": "investigation-summary",
            "recordType": "investigation_summary_md",
            "createdAt": "2026-01-01T00:05:00+00:00",
        }
    ]
    coordinator = InvestigationCoordinator(store, provider, config())
    completed = coordinator.request_health(row(), automatic=True)
    provider.records.extend(
        [
            {
                "executionId": "exec-1",
                "recordId": "mitigation-structured",
                "recordType": "mitigation_summary",
                "createdAt": "2026-01-01T00:06:00+00:00",
                "content": {
                    "mitigation_summary": {
                        "action": "Update the launch template",
                        "reasoning": "Its security group was deleted.",
                    }
                },
            },
            {
                "executionId": "exec-1",
                "recordId": "mitigation-summary",
                "recordType": "mitigation_summary_md",
                "createdAt": "2026-01-01T00:06:00+00:00",
                "content": "# Mitigation Summary",
            },
        ]
    )

    result = coordinator.refresh(completed["investigationId"])

    assert result["mitigationStatus"] == "COMPLETED"
    assert result["mitigationTerminal"] is True
    assert result["outcome"]["mitigation"]["action"] == "Update the launch template"


def test_completed_task_releases_capacity_while_results_are_pending():
    store = MemoryStore()
    provider = Provider(status="COMPLETED")
    coordinator = InvestigationCoordinator(store, provider, config())

    result = coordinator.request_health(row(), automatic=True)

    assert result["workflowState"] == FETCHING_RESULTS
    assert result["leaseHeld"] is False
    assert result["resultFetchStartedAt"]
    assert result["nextReconcileAt"]


def test_completed_task_fails_after_result_fetch_deadline():
    store = MemoryStore()
    provider = Provider(status="COMPLETED")
    coordinator = InvestigationCoordinator(store, provider, config(max_age_minutes=1))
    pending = coordinator.request_health(row(), automatic=True)
    expired = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    store.update_meta(pending["investigationId"], {"resultFetchStartedAt": expired})

    result = coordinator.reconcile(store.get_meta(pending["investigationId"]))

    assert result["workflowState"] == FAILED
    assert result["reason"] == "PROVIDER_RESULTS_UNAVAILABLE"
    assert result["leaseHeld"] is False


def test_running_reconciliation_persists_changed_projection_once():
    store = MemoryStore()
    provider = Provider(status="IN_PROGRESS")
    coordinator = InvestigationCoordinator(store, provider, config())
    started = coordinator.request_health(row(), automatic=True)
    provider.records = [
        {
            "recordId": "analysis-1",
            "recordType": "analysis",
            "createdAt": "2026-01-01T00:00:00+00:00",
            "content": {"step": "Examining resources"},
        }
    ]

    first = coordinator.reconcile(store.get_meta(started["investigationId"]))
    second = coordinator.reconcile(store.get_meta(started["investigationId"]))

    assert provider.journal_calls == 2
    assert first["investigationJournalRevision"] == journal_revision(provider.records)
    assert (
        second["investigationJournalRevision"] == first["investigationJournalRevision"]
    )
    assert first["activity"] == []


def test_provider_age_starts_when_task_is_created_after_long_queue_wait():
    store = MemoryStore()
    provider = Provider(covered=())
    coordinator = InvestigationCoordinator(store, provider, config(max_age_minutes=1))
    pending = coordinator.request_health(row(), automatic=True)
    store.update_meta(
        pending["investigationId"],
        {
            "createdAt": (
                datetime.now(timezone.utc) - timedelta(days=1)
            ).isoformat(),
        },
    )
    provider.covered.add("111111111111")

    started = coordinator.reconcile(store.get_meta(pending["investigationId"]))
    result = coordinator.reconcile(store.get_meta(pending["investigationId"]))

    assert started["providerStartedAt"]
    assert result["workflowState"] == "RUNNING"
    assert provider.status == "IN_PROGRESS"


def test_running_reconciliation_updates_when_projection_revision_changes():
    store = MemoryStore()
    provider = Provider(status="IN_PROGRESS")
    coordinator = InvestigationCoordinator(store, provider, config())
    started = coordinator.request_health(row(), automatic=True)
    provider.records = [{"recordId": "one", "content": {"step": 1}}]
    coordinator.reconcile(store.get_meta(started["investigationId"]))
    provider.records.append({"recordId": "two", "content": {"step": 2}})

    result = coordinator.reconcile(store.get_meta(started["investigationId"]))

    assert result["investigationJournalRevision"] == journal_revision(provider.records)


def test_running_reconciliation_survives_transient_journal_error(caplog):
    store = MemoryStore()
    provider = Provider(status="IN_PROGRESS")
    coordinator = InvestigationCoordinator(store, provider, config())
    started = coordinator.request_health(row(), automatic=True)
    provider.journal_error = RuntimeError("temporarily unavailable")

    result = coordinator.reconcile(store.get_meta(started["investigationId"]))

    assert result["workflowState"] == "RUNNING"
    assert result["nextReconcileAt"]
    assert "JournalReadErrors" in caplog.text


def test_journal_revision_is_stable_for_key_order():
    first = journal_revision([{"recordId": "one", "content": {"a": 1, "b": 2}}])
    second = journal_revision([{"content": {"b": 2, "a": 1}, "recordId": "one"}])

    assert first == second


def test_source_hash_is_stable():
    assert source_hash("HEALTH#a#1") == source_hash("HEALTH#a#1")
    assert source_hash("HEALTH#a#1") != source_hash("HEALTH#a#2")


def test_duplicate_completion_event_is_idempotent():
    store = MemoryStore()
    provider = Provider(status="IN_PROGRESS")
    coordinator = InvestigationCoordinator(store, provider, config())
    coordinator.request_health(row(), automatic=True)
    provider.status = "COMPLETED"
    provider.records = [
        {
            "agentSpaceId": "space-1",
            "executionId": "exec-1",
            "recordId": "summary-1",
            "content": {"markdown": "done"},
            "createdAt": "2026-01-01T00:00:00+00:00",
            "recordType": "investigation_summary_md",
        }
    ]
    event = {
        "id": "event-1",
        "detail-type": "Investigation Completed",
        "detail": {
            "metadata": {"task_id": "task-1"},
            "data": {
                "status": "COMPLETED",
                "summary_record_id": "summary-1",
            },
        },
    }

    first = coordinator.handle_provider_event(event)
    second = coordinator.handle_provider_event(event)

    assert first["workflowState"] == COMPLETED
    assert second == {"duplicate": True, "eventId": "event-1"}


def test_mitigation_completed_uses_event_execution_and_summary_ids():
    store = MemoryStore()
    provider = Provider(status="COMPLETED")
    coordinator = InvestigationCoordinator(store, provider, config())
    started = coordinator.request_health(row(), automatic=True)
    provider.records_by_execution["mitigation-exec"] = [
        {
            "executionId": "mitigation-exec",
            "recordId": "structured",
            "recordType": "mitigation_summary",
            "createdAt": "2026-01-01T00:06:00Z",
            "content": {
                "mitigation_summary": {
                    "action": "No change required",
                    "reasoning": "The condition already cleared.",
                }
            },
        },
        {
            "executionId": "mitigation-exec",
            "recordId": "mitigation-summary",
            "recordType": "mitigation_summary_md",
            "createdAt": "2026-01-01T00:06:00Z",
            "content": "# Mitigation Summary",
        },
    ]

    result = coordinator.handle_provider_event(
        {
            "id": "mitigation-event",
            "detail-type": "Mitigation Completed",
            "detail": {
                "metadata": {
                    "task_id": started["providerTaskId"],
                    "execution_id": "mitigation-exec",
                },
                "data": {
                    "status": "COMPLETED",
                    "summary_record_id": "mitigation-summary",
                    "updated_at": "2026-01-01T00:06:00Z",
                },
            },
        }
    )

    assert result["mitigationExecutionId"] == "mitigation-exec"
    assert result["mitigationSummaryRecordId"] == "mitigation-summary"
    assert result["outcome"]["mitigation"] == {
        "status": "COMPLETED",
        "terminal": True,
        "action": "No change required",
        "description": "The condition already cleared.",
        "updatedAt": "2026-01-01T00:06:00Z",
    }


def test_mitigation_completed_schedules_retry_when_exact_summary_is_missing():
    store = MemoryStore()
    provider = Provider(status="IN_PROGRESS")
    coordinator = InvestigationCoordinator(store, provider, config())
    started = coordinator.request_health(row(), automatic=True)
    provider.records_by_execution["mitigation-exec"] = []

    result = coordinator.handle_provider_event(
        {
            "id": "mitigation-event",
            "detail-type": "Mitigation Completed",
            "detail": {
                "metadata": {
                    "task_id": started["providerTaskId"],
                    "execution_id": "mitigation-exec",
                },
                "data": {
                    "status": "COMPLETED",
                    "summary_record_id": "missing",
                },
            },
        }
    )

    assert result["mitigationStatus"] == MITIGATION_FETCHING
    assert result["mitigationTerminal"] is False
    assert "MITIGATION#mitigation-exec" in store.mitigation_work
    assert "mitigation-event" in store.events


def test_mitigation_in_progress_reconciles_without_completion_event():
    store = MemoryStore()
    provider = Provider(status="COMPLETED")
    coordinator = InvestigationCoordinator(store, provider, config())
    started = coordinator.request_health(row(), automatic=True)

    result = coordinator.handle_provider_event(
        {
            "id": "mitigation-started",
            "detail-type": "Mitigation In Progress",
            "detail": {
                "metadata": {
                    "task_id": started["providerTaskId"],
                    "execution_id": "mitigation-exec",
                },
                "data": {
                    "status": "IN_PROGRESS",
                    "updated_at": "2026-01-01T00:05:00Z",
                },
            },
        }
    )

    assert result["mitigationStatus"] == "IN_PROGRESS"
    assert result["mitigationTerminal"] is False
    assert "MITIGATION#mitigation-exec" in store.mitigation_work

    provider.records_by_execution["mitigation-exec"] = [
        {
            "executionId": "mitigation-exec",
            "recordId": "structured",
            "recordType": "mitigation_summary",
            "content": {
                "mitigation_summary": {
                    "action": "Restore the ingress rule",
                    "reasoning": "The missing rule caused the incident.",
                }
            },
        },
        {
            "executionId": "mitigation-exec",
            "recordId": "mitigation-summary",
            "recordType": "mitigation_summary_md",
            "content": "# Mitigation Summary",
        },
    ]

    reconciled = coordinator.reconcile_due()[0]

    assert reconciled["mitigationStatus"] == "COMPLETED"
    assert reconciled["mitigationTerminal"] is True
    assert reconciled["mitigationSummaryRecordId"] == "mitigation-summary"
    assert reconciled["outcome"]["mitigation"]["action"] == (
        "Restore the ingress rule"
    )
    assert store.mitigation_work == {}


def test_scheduled_reconciliation_finishes_deferred_mitigation():
    store = MemoryStore()
    provider = Provider(status="IN_PROGRESS")
    coordinator = InvestigationCoordinator(store, provider, config())
    started = coordinator.request_health(row(), automatic=True)
    event = {
        "id": "mitigation-event",
        "detail-type": "Mitigation Completed",
        "detail": {
            "metadata": {
                "task_id": started["providerTaskId"],
                "execution_id": "mitigation-exec",
            },
            "data": {
                "status": "COMPLETED",
                "summary_record_id": "mitigation-summary",
            },
        },
    }
    coordinator.handle_provider_event(event)
    provider.records_by_execution["mitigation-exec"] = [
        {
            "executionId": "mitigation-exec",
            "recordId": "mitigation-summary",
            "recordType": "mitigation_summary_md",
            "content": "## Action\nNo change required\n## Reasoning\nAlready clear",
        }
    ]

    results = coordinator.reconcile_due()

    assert results[0]["mitigationStatus"] == "COMPLETED"
    assert results[0]["mitigationTerminal"] is True
    assert store.mitigation_work == {}


def test_deferred_mitigation_stops_after_result_fetch_deadline():
    store = MemoryStore()
    provider = Provider(status="IN_PROGRESS")
    coordinator = InvestigationCoordinator(store, provider, config(max_age_minutes=1))
    started = coordinator.request_health(row(), automatic=True)
    coordinator.handle_provider_event(
        {
            "id": "mitigation-event",
            "detail-type": "Mitigation Completed",
            "detail": {
                "metadata": {
                    "task_id": started["providerTaskId"],
                    "execution_id": "mitigation-exec",
                },
                "data": {
                    "status": "COMPLETED",
                    "summary_record_id": "missing",
                },
            },
        }
    )
    work = store.mitigation_work["MITIGATION#mitigation-exec"]
    work["resultFetchStartedAt"] = (
        datetime.now(timezone.utc) - timedelta(minutes=2)
    ).isoformat()
    store.put_mitigation_work(work)

    result = coordinator.reconcile_due()[0]

    assert result["mitigationStatus"] == "RESULTS_UNAVAILABLE"
    assert result["mitigationTerminal"] is True
    assert store.mitigation_work == {}


def test_duplicate_mitigation_completion_preserves_fetch_deadline():
    store = MemoryStore()
    provider = Provider(status="IN_PROGRESS")
    coordinator = InvestigationCoordinator(store, provider, config(max_age_minutes=1))
    started = coordinator.request_health(row(), automatic=True)
    event = {
        "id": "mitigation-event-1",
        "detail-type": "Mitigation Completed",
        "detail": {
            "metadata": {
                "task_id": started["providerTaskId"],
                "execution_id": "mitigation-exec",
            },
            "data": {
                "status": "COMPLETED",
                "summary_record_id": "missing",
                "updated_at": "2026-01-01T00:06:00Z",
            },
        },
    }
    coordinator.handle_provider_event(event)
    work = store.mitigation_work["MITIGATION#mitigation-exec"]
    first_started_at = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    work["resultFetchStartedAt"] = first_started_at
    store.put_mitigation_work(work)

    event["id"] = "mitigation-event-2"
    coordinator.handle_provider_event(event)

    assert (
        store.mitigation_work["MITIGATION#mitigation-exec"]["resultFetchStartedAt"]
        == first_started_at
    )


def test_stale_mitigation_work_cannot_overwrite_newer_result():
    store = MemoryStore()
    provider = Provider(status="IN_PROGRESS")
    coordinator = InvestigationCoordinator(store, provider, config())
    started = coordinator.request_health(row(), automatic=True)
    investigation_id = started["investigationId"]
    stale_work = coordinator._mitigation_work(
        store.get_meta(investigation_id),
        execution_id="old-exec",
        summary_record_id="old-summary",
        updated_at="2026-01-01T00:05:00+00:00",
    )
    store.put_mitigation_work(stale_work)
    store.update_meta(
        investigation_id,
        {
            "mitigationExecutionId": "new-exec",
            "mitigationUpdatedAt": "2026-01-01T00:06:00+00:00",
            "mitigationStatus": "COMPLETED",
            "mitigationTerminal": True,
            "outcome": {
                "mitigation": {
                    "status": "COMPLETED",
                    "action": "New result",
                }
            },
        },
    )
    provider.records_by_execution["old-exec"] = [
        {
            "executionId": "old-exec",
            "recordId": "old-summary",
            "recordType": "mitigation_summary_md",
            "content": "## Action\nOld result",
        }
    ]

    result = coordinator._reconcile_mitigation_work(stale_work)

    assert result["mitigationExecutionId"] == "new-exec"
    assert result["outcome"]["mitigation"]["action"] == "New result"
    assert "MITIGATION#old-exec" not in store.mitigation_work


def test_naive_fetch_timestamp_is_treated_as_utc(monkeypatch):
    coordinator = InvestigationCoordinator(
        MemoryStore(), Provider(), config(max_age_minutes=1)
    )
    monkeypatch.setattr(
        coordinator_module,
        "utc_now",
        lambda: datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc),
    )

    assert coordinator._fetch_deadline_expired("2026-01-01T00:00:00") is True


def test_reconcile_due_rotates_state_priority(monkeypatch):
    store = MemoryStore()
    coordinator = InvestigationCoordinator(store, Provider(), config())
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(coordinator_module, "utc_now", lambda: start)
    coordinator.reconcile_due()
    first = store.due_states

    monkeypatch.setattr(
        coordinator_module,
        "utc_now",
        lambda: start + timedelta(seconds=60),
    )
    coordinator.reconcile_due()

    assert store.due_states != first
    assert set(store.due_states) == set(first)


def test_reconcile_due_raises_after_recording_item_failure():
    class FailingProvider(Provider):
        def get_task(self, task_id):
            raise RuntimeError("provider unavailable")

    store = MemoryStore()
    coordinator = InvestigationCoordinator(store, FailingProvider(), config())
    started = coordinator.request_health(row(), automatic=True)
    store.due_items = [store.get_meta(started["investigationId"])]

    with pytest.raises(RuntimeError, match="reconciliation item"):
        coordinator.reconcile_due()

    failed = store.get_meta(started["investigationId"])
    assert failed["lastErrorType"] == "RuntimeError"
    assert failed["nextReconcileAt"]


def test_completion_event_waits_when_task_snapshot_is_not_terminal_yet():
    store = MemoryStore()
    provider = Provider(status="IN_PROGRESS")
    coordinator = InvestigationCoordinator(store, provider, config())
    coordinator.request_health(row(), automatic=True)

    result = coordinator.handle_provider_event(
        {
            "id": "event-eventually-consistent",
            "detail-type": "Investigation Completed",
            "detail": {
                "metadata": {"task_id": "task-1"},
                "data": {"status": "COMPLETED"},
            },
        }
    )

    assert result["workflowState"] == "RUNNING"
    assert result["providerStatus"] == "IN_PROGRESS"
    assert result["leaseHeld"] is True


def test_unknown_linked_task_imports_and_reconciles_primary():
    class LinkedProvider(Provider):
        def get_task(self, task_id):
            if task_id == "linked-1":
                return ProviderTask(
                    TaskReference("linked-1", "exec-linked", "LINKED", "primary-1")
                )
            return ProviderTask(TaskReference("primary-1", "exec-primary", "COMPLETED"))

    store = MemoryStore()
    provider = LinkedProvider(status="COMPLETED")
    provider.records = [
        {
            "agentSpaceId": "space-1",
            "executionId": "exec-primary",
            "recordId": "summary-primary",
            "content": {"markdown": "primary result"},
            "createdAt": "2026-01-01T00:00:00+00:00",
            "recordType": "investigation_summary_md",
        }
    ]
    coordinator = InvestigationCoordinator(store, provider, config())

    result = coordinator.handle_provider_event(
        {
            "id": "linked-event",
            "detail-type": "Investigation Linked",
            "detail": {
                "metadata": {"task_id": "linked-1"},
                "data": {"status": "LINKED"},
            },
        }
    )

    assert result["providerTaskId"] == "primary-1"
    assert result["linkedTaskId"] == "linked-1"
    assert result["workflowState"] == COMPLETED


def test_known_linked_primary_releases_duplicate_lease_and_reuses_pointer():
    class LinkedProvider(Provider):
        def get_task(self, task_id):
            return ProviderTask(
                TaskReference(task_id, "exec-linked", "LINKED", "primary-1")
            )

    store = MemoryStore()
    primary = {
        "PK": "INV#primary-investigation",
        "SK": "META",
        "investigationId": "primary-investigation",
        "providerTaskId": "primary-1",
        "workflowState": "RUNNING",
    }
    store.metas["primary-investigation"] = deepcopy(primary)
    store.tasks["primary-1"] = "primary-investigation"
    provider = LinkedProvider()
    coordinator = InvestigationCoordinator(store, provider, config())
    linked = coordinator.request_health(row(), automatic=True)

    result = coordinator.reconcile(store.get_meta(linked["investigationId"]))

    linked_meta = store.get_meta(linked["investigationId"])
    pointer = next(iter(store.pointers.values()))
    assert result["investigationId"] == "primary-investigation"
    assert linked_meta["leaseHeld"] is False
    assert linked_meta["reason"] == "LINKED_TO_EXISTING_INVESTIGATION"
    assert pointer["investigationId"] == "primary-investigation"


def test_terminal_transaction_uses_only_values_referenced_by_each_update():
    class Resource:
        @staticmethod
        def Table(name):
            return object()

    class Client:
        class exceptions:
            TransactionCanceledException = RuntimeError

        def __init__(self):
            self.request = None

        def transact_write_items(self, **kwargs):
            self.request = kwargs

    client = Client()
    store = InvestigationStore("investigations", resource=Resource(), client=client)

    store.transition_and_release(
        "investigation-1",
        "space-1",
        {"workflowState": COMPLETED, "providerStatus": "COMPLETED"},
    )

    updates = client.request["TransactItems"]
    meta_values = updates[0]["Update"]["ExpressionAttributeValues"]
    capacity_values = updates[1]["Update"]["ExpressionAttributeValues"]
    assert ":true" in meta_values
    assert ":zero" not in meta_values
    assert ":one" not in meta_values
    assert {":zero", ":one"}.issubset(capacity_values)
