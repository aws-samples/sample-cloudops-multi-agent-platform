"""Shared persistence and orchestration for AWS DevOps Agent investigations."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from shared.devops_agent_provider import (
    AmbiguousCreateError,
    DevOpsAgentProvider,
    ProviderRateLimitError,
    ProviderTask,
)
from shared.devops_agent_projection import (
    merge_outcome,
    project_investigation,
    project_mitigation,
)
from shared.devops_agent_redaction import redact

logger = logging.getLogger(__name__)

PENDING_ACCOUNT_ASSOCIATION = "PENDING_ACCOUNT_ASSOCIATION"
PENDING_CONCURRENCY_LIMIT = "PENDING_CONCURRENCY_LIMIT"
PENDING_RATE_LIMIT = "PENDING_RATE_LIMIT"
CREATING = "CREATING"
RUNNING = "RUNNING"
FETCHING_RESULTS = "FETCHING_RESULTS"
WAITING_CUSTOMER_APPROVAL = "WAITING_CUSTOMER_APPROVAL"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
EXPIRING = "EXPIRING"
MITIGATION_FETCHING = "MITIGATION_FETCHING"

TERMINAL_PROVIDER_STATUSES = {
    "COMPLETED",
    "FAILED",
    "TIMED_OUT",
    "CANCELED",
    "SKIPPED",
}
TERMINAL_WORKFLOW_STATES = {COMPLETED, FAILED}
DUE_STATES = (
    PENDING_ACCOUNT_ASSOCIATION,
    PENDING_CONCURRENCY_LIMIT,
    PENDING_RATE_LIMIT,
    CREATING,
    RUNNING,
    FETCHING_RESULTS,
    WAITING_CUSTOMER_APPROVAL,
    EXPIRING,
    MITIGATION_FETCHING,
)

_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def source_key(event_arn: str, account_id: str) -> str:
    return f"HEALTH#{event_arn}#{account_id}"


def operational_source_key(account_id: str, source_id: str) -> str:
    return f"OPERATIONAL#{account_id}#{source_id}"


def source_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def journal_revision(records: list[dict[str, Any]]) -> str:
    """Hash the complete, serialized provider journal response."""
    canonical = json.dumps(
        records, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ddb_map(item: dict[str, Any]) -> dict[str, Any]:
    return {key: _SERIALIZER.serialize(value) for key, value in item.items()}


def _from_ddb_map(item: dict[str, Any]) -> dict[str, Any]:
    return {key: _DESERIALIZER.deserialize(value) for key, value in item.items()}


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else str(value)
    if isinstance(value, dict):
        return {key: _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(val) for val in value]
    return value


@dataclass(frozen=True)
class CoordinatorConfig:
    agent_space_id: str
    integration_enabled: bool = False
    automatic_health_enabled: bool = False
    max_concurrency: int = 2
    automatic_daily_budget: int = 10
    max_age_minutes: int = 120
    reconciliation_seconds: int = 60

    @classmethod
    def from_env(cls) -> "CoordinatorConfig":
        def flag(name: str) -> bool:
            return os.environ.get(name, "false").lower() == "true"

        return cls(
            agent_space_id=os.environ.get("DEVOPS_AGENT_SPACE_ID", ""),
            integration_enabled=flag("DEVOPS_AGENT_INTEGRATION_ENABLED"),
            automatic_health_enabled=flag("DEVOPS_AGENT_HEALTH_AUTOMATIC_ENABLED"),
            max_concurrency=int(os.environ.get("DEVOPS_AGENT_MAX_CONCURRENCY", "2")),
            automatic_daily_budget=int(
                os.environ.get("DEVOPS_AGENT_AUTOMATIC_DAILY_BUDGET", "10")
            ),
            max_age_minutes=int(os.environ.get("DEVOPS_AGENT_MAX_AGE_MINUTES", "120")),
            reconciliation_seconds=int(
                os.environ.get("DEVOPS_AGENT_RECONCILIATION_SECONDS", "60")
            ),
        )


class InvestigationStore:
    """DynamoDB persistence with transactional claims and provider-task leases."""

    def __init__(self, table_name: str, *, resource=None, client=None):
        resource = resource or boto3.resource("dynamodb")
        self.table_name = table_name
        self.table = resource.Table(table_name)
        self.client = client or boto3.client(
            "dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )

    def get(self, pk: str, sk: str) -> dict[str, Any] | None:
        return self.table.get_item(Key={"PK": pk, "SK": sk}).get("Item")

    def get_meta(self, investigation_id: str) -> dict[str, Any] | None:
        return self.get(f"INV#{investigation_id}", "META")

    def get_pointer(self, source: str) -> dict[str, Any] | None:
        return self.get(f"SOURCE#{source_hash(source)}", "POINTER")

    def claim_source(self, meta: dict[str, Any], pointer: dict[str, Any]) -> bool:
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": _ddb_map(meta),
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": _ddb_map(pointer),
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                ]
            )
            return True
        except self.client.exceptions.TransactionCanceledException:
            return False

    def update_meta(
        self, investigation_id: str, values: dict[str, Any], *, remove=()
    ) -> dict[str, Any]:
        names = {}
        expression_values = {}
        sets = []
        for index, (name, value) in enumerate(values.items()):
            name_key = f"#n{index}"
            value_key = f":v{index}"
            names[name_key] = name
            expression_values[value_key] = value
            sets.append(f"{name_key} = {value_key}")
        removes = []
        for index, name in enumerate(remove, start=len(names)):
            name_key = f"#n{index}"
            names[name_key] = name
            removes.append(name_key)
        parts = []
        if sets:
            parts.append("SET " + ", ".join(sets))
        if removes:
            parts.append("REMOVE " + ", ".join(removes))
        response = self.table.update_item(
            Key={"PK": f"INV#{investigation_id}", "SK": "META"},
            UpdateExpression=" ".join(parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=expression_values or None,
            ReturnValues="ALL_NEW",
        )
        return response["Attributes"]

    def acquire_lease(
        self,
        investigation_id: str,
        agent_space_id: str,
        *,
        consume_daily_budget: bool,
        max_concurrency: int,
        daily_budget: int,
        next_reconcile_at: str,
    ) -> str | None:
        now = iso_now()
        meta_update = (
            "SET leaseHeld = :true, workflowState = :state, "
            "updatedAt = :now, nextReconcileAt = :next"
        )
        if consume_daily_budget:
            meta_update += ", automaticBudgetConsumed = :true"
        items = [
            {
                "Update": {
                    "TableName": self.table_name,
                    "Key": _ddb_map(
                        {
                            "PK": f"CAPACITY#{agent_space_id}",
                            "SK": "COUNTER",
                        }
                    ),
                    "UpdateExpression": (
                        "SET activeCount = if_not_exists(activeCount, :zero) + :one, "
                        "updatedAt = :now"
                    ),
                    "ConditionExpression": (
                        "attribute_not_exists(activeCount) OR activeCount < :limit"
                    ),
                    "ExpressionAttributeValues": _ddb_map(
                        {
                            ":zero": 0,
                            ":one": 1,
                            ":now": now,
                            ":limit": max_concurrency,
                        }
                    ),
                }
            },
            {
                "Update": {
                    "TableName": self.table_name,
                    "Key": _ddb_map({"PK": f"INV#{investigation_id}", "SK": "META"}),
                    "UpdateExpression": meta_update,
                    "ConditionExpression": "attribute_not_exists(leaseHeld) OR leaseHeld = :false",
                    "ExpressionAttributeValues": _ddb_map(
                        {
                            ":true": True,
                            ":false": False,
                            ":state": CREATING,
                            ":now": now,
                            ":next": next_reconcile_at,
                        }
                    ),
                }
            },
        ]
        if consume_daily_budget:
            budget_key = utc_now().strftime("%Y-%m-%d")
            items.append(
                {
                    "Update": {
                        "TableName": self.table_name,
                        "Key": _ddb_map(
                            {"PK": f"BUDGET#{budget_key}", "SK": "COUNTER"}
                        ),
                        "UpdateExpression": (
                            "SET automaticCount = if_not_exists(automaticCount, :zero) "
                            "+ :one, updatedAt = :now"
                        ),
                        "ConditionExpression": (
                            "attribute_not_exists(automaticCount) OR "
                            "automaticCount < :limit"
                        ),
                        "ExpressionAttributeValues": _ddb_map(
                            {
                                ":zero": 0,
                                ":one": 1,
                                ":now": now,
                                ":limit": daily_budget,
                            }
                        ),
                    }
                }
            )
        try:
            self.client.transact_write_items(TransactItems=items)
            return None
        except self.client.exceptions.TransactionCanceledException:
            current = self.get_meta(investigation_id) or {}
            if current.get("leaseHeld"):
                return "busy"
            capacity = self.get(f"CAPACITY#{agent_space_id}", "COUNTER") or {}
            if int(capacity.get("activeCount", 0)) >= max_concurrency:
                return "capacity"
            return "budget" if consume_daily_budget else "capacity"

    def transition_and_release(
        self,
        investigation_id: str,
        agent_space_id: str,
        values: dict[str, Any],
        *,
        keep_due: bool = False,
    ) -> None:
        now = iso_now()
        values = {**values, "leaseHeld": False, "updatedAt": now}
        names = {f"#n{i}": name for i, name in enumerate(values)}
        expression_values = {f":v{i}": value for i, value in enumerate(values.values())}
        expression_values[":true"] = True
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self.table_name,
                            "Key": _ddb_map(
                                {"PK": f"INV#{investigation_id}", "SK": "META"}
                            ),
                            "UpdateExpression": (
                                "SET "
                                + ", ".join(
                                    f"{name_key} = :v{i}"
                                    for i, name_key in enumerate(names)
                                )
                                + ("" if keep_due else " REMOVE nextReconcileAt")
                            ),
                            "ConditionExpression": "leaseHeld = :true",
                            "ExpressionAttributeNames": names,
                            "ExpressionAttributeValues": _ddb_map(expression_values),
                        }
                    },
                    {
                        "Update": {
                            "TableName": self.table_name,
                            "Key": _ddb_map(
                                {
                                    "PK": f"CAPACITY#{agent_space_id}",
                                    "SK": "COUNTER",
                                }
                            ),
                            "UpdateExpression": (
                                "SET activeCount = activeCount - :one, updatedAt = :v1"
                            ),
                            "ConditionExpression": "activeCount > :zero",
                            "ExpressionAttributeValues": _ddb_map(
                                {":one": 1, ":zero": 0, ":v1": now}
                            ),
                        }
                    },
                ]
            )
        except self.client.exceptions.TransactionCanceledException:
            current = self.get_meta(investigation_id) or {}
            if current.get("leaseHeld"):
                raise
            self.update_meta(
                investigation_id,
                values,
                remove=() if keep_due else ("nextReconcileAt",),
            )

    def update_pointer(self, source: str, investigation_id: str) -> None:
        self.table.update_item(
            Key={"PK": f"SOURCE#{source_hash(source)}", "SK": "POINTER"},
            UpdateExpression=(
                "SET investigationId = :id, updatedAt = :now REMOVE active"
            ),
            ExpressionAttributeValues={
                ":id": investigation_id,
                ":now": iso_now(),
            },
        )

    def find_by_task(self, task_id: str) -> dict[str, Any] | None:
        response = self.table.query(
            IndexName="TaskIdIndex",
            KeyConditionExpression=Key("providerTaskId").eq(task_id),
            Limit=1,
        )
        return next(iter(response.get("Items", [])), None)

    def due(
        self,
        now: str,
        limit: int = 100,
        *,
        states: tuple[str, ...] = DUE_STATES,
    ) -> list[dict[str, Any]]:
        items = []
        for state in states:
            response = self.table.query(
                IndexName="WorkIndex",
                KeyConditionExpression=(
                    Key("workflowState").eq(state) & Key("nextReconcileAt").lte(now)
                ),
                Limit=max(1, limit - len(items)),
            )
            items.extend(response.get("Items", []))
            if len(items) >= limit:
                break
        return items

    def put_mitigation_work(
        self,
        item: dict[str, Any],
        *,
        expected: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        kwargs: dict[str, Any] = {"Item": item}
        if expected is not None:
            kwargs["ConditionExpression"] = Attr("mitigationSummaryRecordId").eq(
                expected.get("mitigationSummaryRecordId", "")
            ) & Attr("mitigationUpdatedAt").eq(expected.get("mitigationUpdatedAt", ""))
        try:
            self.table.put_item(**kwargs)
            return item
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return None

    def get_mitigation_work(
        self, investigation_id: str, execution_id: str
    ) -> dict[str, Any] | None:
        return self.get(
            f"INV#{investigation_id}",
            f"MITIGATION#{execution_id}",
        )

    def delete_mitigation_work(self, item: dict[str, Any]) -> None:
        try:
            self.table.delete_item(
                Key={"PK": item["PK"], "SK": item["SK"]},
                ConditionExpression=(
                    Attr("mitigationSummaryRecordId").eq(
                        item.get("mitigationSummaryRecordId", "")
                    )
                    & Attr("mitigationUpdatedAt").eq(
                        item.get("mitigationUpdatedAt", "")
                    )
                ),
            )
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            # A newer completion event replaced this work item.
            return

    def mark_event(self, event_id: str, ttl: int) -> bool:
        try:
            self.table.put_item(
                Item={
                    "PK": f"EVENT#{event_id}",
                    "SK": "PROCESSED",
                    "ttl": ttl,
                    "createdAt": iso_now(),
                },
                ConditionExpression="attribute_not_exists(PK)",
            )
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def unmark_event(self, event_id: str) -> None:
        self.table.delete_item(Key={"PK": f"EVENT#{event_id}", "SK": "PROCESSED"})


class InvestigationCoordinator:
    def __init__(
        self,
        store: InvestigationStore,
        provider: DevOpsAgentProvider,
        config: CoordinatorConfig,
    ):
        self.store = store
        self.provider = provider
        self.config = config

    def _next(self, seconds: int | None = None) -> str:
        delay = seconds or self.config.reconciliation_seconds
        return (utc_now() + timedelta(seconds=delay)).isoformat()

    def _fetch_deadline_expired(self, started_at: str) -> bool:
        try:
            started = datetime.fromisoformat(started_at)
        except (TypeError, ValueError):
            return True
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return utc_now() - started.astimezone(timezone.utc) > timedelta(
            minutes=self.config.max_age_minutes
        )

    @staticmethod
    def _provider_time(value: Any) -> str:
        raw = str(value or "").strip()
        try:
            timestamp = datetime.fromisoformat(raw)
        except ValueError:
            return iso_now()
        return raw

    @staticmethod
    def _time_is_after(left: str, right: str) -> bool:
        try:
            left_time = datetime.fromisoformat(left)
            right_time = datetime.fromisoformat(right)
        except ValueError:
            return left > right
        if left_time.tzinfo is None:
            left_time = left_time.replace(tzinfo=timezone.utc)
        if right_time.tzinfo is None:
            right_time = right_time.replace(tzinfo=timezone.utc)
        return left_time.astimezone(timezone.utc) > right_time.astimezone(timezone.utc)

    @staticmethod
    def _mitigation_work_is_stale(meta: dict[str, Any], work: dict[str, Any]) -> bool:
        current_updated_at = str(meta.get("mitigationUpdatedAt", ""))
        work_updated_at = str(work.get("mitigationUpdatedAt", ""))
        current_execution_id = str(meta.get("mitigationExecutionId", ""))
        work_execution_id = str(work.get("mitigationExecutionId", ""))
        if InvestigationCoordinator._time_is_after(current_updated_at, work_updated_at):
            return True
        if current_updated_at == work_updated_at:
            if current_execution_id != work_execution_id:
                return True
            if meta.get("mitigationTerminal"):
                return True
        return False

    @staticmethod
    def _request(meta: dict[str, Any]) -> dict[str, Any]:
        return {
            "investigationId": meta["investigationId"],
            "title": meta["requestTitle"],
            "description": meta["requestDescription"],
            "priority": meta["requestPriority"],
        }

    @staticmethod
    def _workflow_state(provider_status: str) -> str:
        if provider_status == "PENDING_CUSTOMER_APPROVAL":
            return WAITING_CUSTOMER_APPROVAL
        if provider_status == "COMPLETED":
            return FETCHING_RESULTS
        if provider_status in {"FAILED", "TIMED_OUT", "CANCELED", "SKIPPED"}:
            return FAILED
        return RUNNING

    def request_health(
        self,
        row: dict[str, Any],
        *,
        automatic: bool,
        force_recheck: bool = False,
    ) -> dict[str, Any] | None:
        event_arn = str(row.get("eventArn", ""))
        account_id = str(row.get("accountId", ""))
        if not event_arn or not account_id:
            return None
        if not self.config.integration_enabled:
            return None
        if automatic:
            if not self.config.automatic_health_enabled:
                return None
            if (
                row.get("actionability") != "ACTION_REQUIRED"
                and row.get("eventTypeCategory") != "issue"
            ):
                return None
            if str(row.get("statusCode", "")).lower() == "closed":
                return None

        source = source_key(event_arn, account_id)
        pointer = self.store.get_pointer(source)
        if pointer:
            meta = self.store.get_meta(pointer["investigationId"])
            if (
                meta
                and force_recheck
                and meta.get("workflowState")
                in {
                    PENDING_ACCOUNT_ASSOCIATION,
                    PENDING_CONCURRENCY_LIMIT,
                    PENDING_RATE_LIMIT,
                }
            ):
                return self.reconcile(meta, force_coverage=True)
            return _json_safe(meta) if meta else None

        investigation_id = str(uuid.uuid4())
        now = iso_now()
        description = (
            f"AWS Health event {event_arn} affects account {account_id}. "
            f"Service: {row.get('service', 'UNKNOWN')}. "
            f"Event type: {row.get('eventTypeCode', '')}. "
            f"Description: {row.get('description', '')}"
        )[:4000]
        meta = {
            "PK": f"INV#{investigation_id}",
            "SK": "META",
            "investigationId": investigation_id,
            "source": "AWS_HEALTH",
            "sourceKey": source,
            "eventArn": event_arn,
            "accountId": account_id,
            "automatic": automatic,
            "workflowState": PENDING_ACCOUNT_ASSOCIATION,
            "providerStatus": "NOT_CREATED",
            "requestTitle": (
                f"AWS Health: {row.get('service', 'UNKNOWN')} "
                f"{row.get('eventTypeCode', 'event')}"
            )[:255],
            "requestDescription": description,
            "requestPriority": (
                row.get("riskLevel")
                if row.get("riskLevel")
                in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL"}
                else "HIGH"
            ),
            "createdAt": now,
            "updatedAt": now,
            "nextReconcileAt": now,
            "leaseHeld": False,
        }
        pointer_item = {
            "PK": f"SOURCE#{source_hash(source)}",
            "SK": "POINTER",
            "sourceKey": source,
            "investigationId": investigation_id,
            "createdAt": now,
            "updatedAt": now,
        }
        if not self.store.claim_source(meta, pointer_item):
            winner = self.store.get_pointer(source)
            return (
                _json_safe(self.store.get_meta(winner["investigationId"]))
                if winner
                else None
            )
        return self._start(meta, force_coverage=force_recheck)

    def request_operational_issue(
        self,
        *,
        account_id: str,
        source_id: str,
        title: str,
        description: str,
        priority: str = "HIGH",
        force_recheck: bool = False,
    ) -> dict[str, Any] | None:
        if not self.config.integration_enabled:
            return None

        account_id = str(account_id).strip()
        source_id = str(source_id).strip()
        title = str(title).strip()
        description = str(description).strip()
        if not account_id or not source_id or not title or not description:
            return None

        source = operational_source_key(account_id, source_id[:1000])
        pointer = self.store.get_pointer(source)
        if pointer:
            meta = self.store.get_meta(pointer["investigationId"])
            if (
                meta
                and force_recheck
                and meta.get("workflowState")
                in {
                    PENDING_ACCOUNT_ASSOCIATION,
                    PENDING_CONCURRENCY_LIMIT,
                    PENDING_RATE_LIMIT,
                }
            ):
                return self.reconcile(meta, force_coverage=True)
            return _json_safe(meta) if meta else None

        investigation_id = str(uuid.uuid4())
        now = iso_now()
        meta = {
            "PK": f"INV#{investigation_id}",
            "SK": "META",
            "investigationId": investigation_id,
            "source": "OPERATIONAL_ISSUE",
            "sourceKey": source,
            "externalSourceId": source_id[:1000],
            "accountId": account_id,
            "automatic": False,
            "workflowState": PENDING_ACCOUNT_ASSOCIATION,
            "providerStatus": "NOT_CREATED",
            "requestTitle": title[:400],
            "requestDescription": description[:10000],
            "requestPriority": (
                priority
                if priority in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL"}
                else "HIGH"
            ),
            "createdAt": now,
            "updatedAt": now,
            "nextReconcileAt": now,
            "leaseHeld": False,
        }
        pointer_item = {
            "PK": f"SOURCE#{source_hash(source)}",
            "SK": "POINTER",
            "sourceKey": source,
            "investigationId": investigation_id,
            "createdAt": now,
            "updatedAt": now,
        }
        if not self.store.claim_source(meta, pointer_item):
            winner = self.store.get_pointer(source)
            return (
                _json_safe(self.store.get_meta(winner["investigationId"]))
                if winner
                else None
            )
        return self._start(meta, force_coverage=force_recheck)

    def _start(
        self, meta: dict[str, Any], *, force_coverage: bool = False
    ) -> dict[str, Any]:
        covered = self.provider.list_covered_accounts(force_refresh=force_coverage)
        if meta["accountId"] not in covered:
            return _json_safe(
                self.store.update_meta(
                    meta["investigationId"],
                    {
                        "workflowState": PENDING_ACCOUNT_ASSOCIATION,
                        "reason": "SOURCE_ACCOUNT_NOT_ASSOCIATED",
                        "nextReconcileAt": self._next(),
                        "updatedAt": iso_now(),
                    },
                )
            )
        failure = self.store.acquire_lease(
            meta["investigationId"],
            self.config.agent_space_id,
            consume_daily_budget=(
                bool(meta.get("automatic"))
                and not bool(meta.get("automaticBudgetConsumed"))
            ),
            max_concurrency=self.config.max_concurrency,
            daily_budget=self.config.automatic_daily_budget,
            next_reconcile_at=self._next(),
        )
        if failure:
            if failure == "busy":
                return _json_safe(self.store.get_meta(meta["investigationId"]) or meta)
            state = (
                PENDING_CONCURRENCY_LIMIT
                if failure == "capacity"
                else PENDING_RATE_LIMIT
            )
            reason = (
                "AGENT_SPACE_CONCURRENCY_LIMIT"
                if failure == "capacity"
                else "AUTOMATIC_DAILY_BUDGET"
            )
            return _json_safe(
                self.store.update_meta(
                    meta["investigationId"],
                    {
                        "workflowState": state,
                        "reason": reason,
                        "nextReconcileAt": self._next(),
                        "updatedAt": iso_now(),
                    },
                )
            )
        current = self.store.get_meta(meta["investigationId"]) or meta
        return self._create(current)

    def _create(self, meta: dict[str, Any]) -> dict[str, Any]:
        try:
            task = self.provider.create_investigation(self._request(meta))
        except AmbiguousCreateError as exc:
            return _json_safe(
                self.store.update_meta(
                    meta["investigationId"],
                    {
                        "workflowState": CREATING,
                        "reason": "AMBIGUOUS_CREATE",
                        "lastError": str(exc)[:1000],
                        "nextReconcileAt": self._next(),
                        "updatedAt": iso_now(),
                    },
                )
            )
        except ProviderRateLimitError as exc:
            self.store.transition_and_release(
                meta["investigationId"],
                self.config.agent_space_id,
                {
                    "workflowState": PENDING_RATE_LIMIT,
                    "providerStatus": "NOT_CREATED",
                    "reason": "PROVIDER_RATE_LIMIT",
                    "lastError": str(exc)[:1000],
                    "nextReconcileAt": self._next(),
                },
                keep_due=True,
            )
            return _json_safe(self.store.get_meta(meta["investigationId"]))
        except Exception as exc:
            self._terminal(
                meta,
                FAILED,
                "CREATE_FAILED",
                provider_status="CREATE_FAILED",
                last_error=str(exc)[:1000],
            )
            raise
        return self._persist_task(meta, task)

    def _persist_task(self, meta: dict[str, Any], task: ProviderTask) -> dict[str, Any]:
        state = self._workflow_state(task.reference.status)
        values = {
            "providerTaskId": task.reference.task_id,
            "providerExecutionId": task.reference.execution_id or "",
            "providerStatus": task.reference.status,
            "workflowState": state,
            "nextReconcileAt": self._next(),
            "updatedAt": iso_now(),
        }
        if not meta.get("providerStartedAt"):
            values["providerStartedAt"] = iso_now()
        updated = self.store.update_meta(meta["investigationId"], values)
        if task.reference.status in TERMINAL_PROVIDER_STATUSES:
            return self._finish_results(updated, task)
        return _json_safe(updated)

    def _sync_journals(
        self, meta: dict[str, Any], execution_id: str | None
    ) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
        """Fetch journals without allowing transient provider errors to fail work."""
        if not execution_id:
            return meta, None
        try:
            records = self.provider.list_journal_records(execution_id)
        except Exception as exc:
            logger.warning(
                json.dumps(
                    {
                        "_aws": {
                            "Timestamp": int(utc_now().timestamp() * 1000),
                            "CloudWatchMetrics": [
                                {
                                    "Namespace": "CloudOps/DevOpsAgent",
                                    "Dimensions": [["Operation"]],
                                    "Metrics": [
                                        {
                                            "Name": "JournalReadErrors",
                                            "Unit": "Count",
                                        }
                                    ],
                                }
                            ],
                        },
                        "Operation": "ListJournalRecords",
                        "JournalReadErrors": 1,
                        "errorType": type(exc).__name__,
                    }
                )
            )
            return meta, None

        revision = journal_revision(records)
        if revision == meta.get("investigationJournalRevision"):
            return meta, records

        projection = project_investigation(records)
        outcome = redact(
            merge_outcome(
                meta.get("outcome"),
                incident=projection["incident"],
                root_cause=projection["rootCause"],
            )
        )
        updated = self.store.update_meta(
            meta["investigationId"],
            {
                "projectionVersion": 1,
                "investigationJournalRevision": revision,
                "outcome": outcome,
                "activity": redact(projection["activity"]),
                "updatedAt": iso_now(),
            },
        )
        return updated, records

    def _terminal(
        self,
        meta: dict[str, Any],
        state: str,
        reason: str,
        *,
        provider_status: str,
        last_error: str = "",
    ) -> dict[str, Any]:
        values = {
            "workflowState": state,
            "providerStatus": provider_status,
            "reason": reason,
            "completedAt": iso_now(),
        }
        if last_error:
            values["lastError"] = last_error
        if meta.get("leaseHeld"):
            self.store.transition_and_release(
                meta["investigationId"], self.config.agent_space_id, values
            )
        else:
            self.store.update_meta(
                meta["investigationId"], values, remove=("nextReconcileAt",)
            )
        self.store.update_pointer(meta["sourceKey"], meta["investigationId"])
        return _json_safe(self.store.get_meta(meta["investigationId"]))

    def _persist_latest_mitigation(
        self,
        meta: dict[str, Any],
        records: list[dict[str, Any]],
        execution_id: str,
    ) -> dict[str, Any]:
        summaries = [
            record
            for record in records
            if record.get("recordType") == "mitigation_summary_md"
            and (
                not execution_id
                or not record.get("executionId")
                or record.get("executionId") == execution_id
            )
        ]
        summary = None
        for candidate in summaries:
            if summary is None or self._time_is_after(
                str(candidate.get("createdAt", "")),
                str(summary.get("createdAt", "")),
            ):
                summary = candidate
        if summary is None:
            return meta

        summary_record_id = str(summary.get("recordId", ""))
        updated_at = str(summary.get("createdAt") or iso_now())
        if (
            meta.get("mitigationTerminal")
            and meta.get("mitigationSummaryRecordId") == summary_record_id
        ):
            return meta
        if self._time_is_after(str(meta.get("mitigationUpdatedAt", "")), updated_at):
            return meta

        mitigation = project_mitigation(
            records,
            execution_id=execution_id,
            summary_record_id=summary_record_id,
            status="COMPLETED",
            updated_at=updated_at,
        )
        if mitigation is None:
            return meta
        outcome = redact(merge_outcome(meta.get("outcome"), mitigation=mitigation))
        return self.store.update_meta(
            meta["investigationId"],
            {
                "projectionVersion": 1,
                "outcome": outcome,
                "mitigationStatus": mitigation["status"],
                "mitigationTerminal": True,
                "mitigationExecutionId": execution_id,
                "mitigationSummaryRecordId": summary_record_id,
                "mitigationUpdatedAt": mitigation["updatedAt"],
                "updatedAt": iso_now(),
            },
        )

    def _finish_results(
        self,
        meta: dict[str, Any],
        task: ProviderTask,
        *,
        summary_record_id: str | None = None,
    ) -> dict[str, Any]:
        status = task.reference.status
        if meta.get("leaseHeld"):
            self.store.transition_and_release(
                meta["investigationId"],
                self.config.agent_space_id,
                {
                    "workflowState": (
                        FETCHING_RESULTS if status == "COMPLETED" else FAILED
                    ),
                    "providerStatus": status,
                    "nextReconcileAt": self._next(),
                },
                keep_due=True,
            )
            meta = self.store.get_meta(meta["investigationId"]) or meta

        execution_id = task.reference.execution_id or meta.get("providerExecutionId")
        if status == "COMPLETED":
            fetch_started_at = str(meta.get("resultFetchStartedAt") or iso_now())
            meta, records = self._sync_journals(meta, execution_id)
            records = records or []
            summary_id = summary_record_id or meta.get("summaryRecordId")
            summaries = [
                record
                for record in records
                if record.get("recordType") == "investigation_summary_md"
                and (
                    not execution_id
                    or not record.get("executionId")
                    or record.get("executionId") == execution_id
                )
            ]
            if summary_id:
                summary = next(
                    (
                        record
                        for record in summaries
                        if record.get("recordId") == summary_id
                    ),
                    None,
                )
            else:
                summary = None
                for candidate in summaries:
                    if summary is None or self._time_is_after(
                        str(candidate.get("createdAt", "")),
                        str(summary.get("createdAt", "")),
                    ):
                        summary = candidate
            if summary is None:
                if self._fetch_deadline_expired(fetch_started_at):
                    return self._terminal(
                        meta,
                        FAILED,
                        "PROVIDER_RESULTS_UNAVAILABLE",
                        provider_status=status,
                    )
                return _json_safe(
                    self.store.update_meta(
                        meta["investigationId"],
                        {
                            "workflowState": FETCHING_RESULTS,
                            "providerStatus": status,
                            "summaryRecordId": summary_id or "",
                            "resultFetchStartedAt": fetch_started_at,
                            "nextReconcileAt": self._next(),
                            "updatedAt": iso_now(),
                        },
                    )
                )
            meta = self._persist_latest_mitigation(meta, records, execution_id)
            self.store.update_meta(
                meta["investigationId"],
                {
                    "providerExecutionId": execution_id,
                    "summaryRecordId": summary.get("recordId", "") if summary else "",
                },
            )
            current = self.store.get_meta(meta["investigationId"]) or meta
            return self._terminal(
                current,
                COMPLETED,
                "PROVIDER_COMPLETED",
                provider_status=status,
            )
        return self._terminal(
            meta,
            FAILED,
            f"PROVIDER_{status}",
            provider_status=status,
        )

    def refresh(self, investigation_id: str) -> dict[str, Any]:
        """Re-read provider journals for one investigation on explicit request."""
        meta = self.store.get_meta(investigation_id)
        if not meta:
            return {"ignored": True, "reason": "INVESTIGATION_NOT_FOUND"}
        if meta.get("workflowState") not in TERMINAL_WORKFLOW_STATES:
            return self.reconcile(meta, force_coverage=True)
        if meta.get("workflowState") != COMPLETED:
            return _json_safe(meta)

        execution_id = str(meta.get("providerExecutionId", ""))
        if not execution_id:
            task_id = str(meta.get("providerTaskId", ""))
            if not task_id:
                return _json_safe(meta)
            task = self.provider.get_task(task_id)
            execution_id = str(task.reference.execution_id or "")
        if not execution_id:
            return _json_safe(meta)

        meta, records = self._sync_journals(meta, execution_id)
        return _json_safe(
            self._persist_latest_mitigation(meta, records or [], execution_id)
        )

    def _reuse_linked_investigation(
        self, linked_meta: dict[str, Any], primary_meta: dict[str, Any]
    ) -> dict[str, Any]:
        """Finish a linked duplicate and route its source to the known primary."""
        values = {
            "workflowState": COMPLETED,
            "providerStatus": "LINKED",
            "reason": "LINKED_TO_EXISTING_INVESTIGATION",
            "linkedInvestigationId": primary_meta["investigationId"],
            "completedAt": iso_now(),
        }
        if linked_meta.get("leaseHeld"):
            self.store.transition_and_release(
                linked_meta["investigationId"],
                self.config.agent_space_id,
                values,
            )
        else:
            self.store.update_meta(
                linked_meta["investigationId"],
                values,
                remove=("nextReconcileAt",),
            )
        self.store.update_pointer(
            linked_meta["sourceKey"], primary_meta["investigationId"]
        )
        return _json_safe(primary_meta)

    def reconcile(
        self, meta: dict[str, Any], *, force_coverage: bool = False
    ) -> dict[str, Any]:
        state = meta.get("workflowState")
        if state in TERMINAL_WORKFLOW_STATES:
            return _json_safe(meta)
        if state == PENDING_ACCOUNT_ASSOCIATION:
            return self._start(meta, force_coverage=True)
        if state in {PENDING_CONCURRENCY_LIMIT, PENDING_RATE_LIMIT}:
            return self._start(meta, force_coverage=force_coverage)
        if state == CREATING and not meta.get("providerTaskId"):
            return self._create(meta)

        task_id = meta.get("providerTaskId")
        if not task_id:
            return self._start(meta, force_coverage=force_coverage)
        task = self.provider.get_task(task_id)
        if task.reference.status == "LINKED" and task.reference.primary_task_id:
            primary = self.store.find_by_task(task.reference.primary_task_id)
            if primary:
                return self._reuse_linked_investigation(meta, primary)
            primary_task = self.provider.get_task(task.reference.primary_task_id)
            meta = self.store.update_meta(
                meta["investigationId"],
                {
                    "providerTaskId": primary_task.reference.task_id,
                    "providerExecutionId": (primary_task.reference.execution_id or ""),
                    "linkedTaskId": task.reference.task_id,
                    "updatedAt": iso_now(),
                },
            )
            task = primary_task

        execution_id = task.reference.execution_id or meta.get("providerExecutionId")
        provider_started_at = meta.get("providerStartedAt")
        if not provider_started_at:
            provider_started_at = iso_now()
            meta = self.store.update_meta(
                meta["investigationId"],
                {"providerStartedAt": provider_started_at, "updatedAt": iso_now()},
            )
        started_at = datetime.fromisoformat(provider_started_at)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        max_age = timedelta(minutes=self.config.max_age_minutes)
        if (
            task.reference.status not in TERMINAL_PROVIDER_STATUSES
            and utc_now() - started_at.astimezone(timezone.utc) > max_age
        ):
            if state != EXPIRING:
                task = self.provider.cancel(task.reference.task_id)
            return _json_safe(
                self.store.update_meta(
                    meta["investigationId"],
                    {
                        "workflowState": EXPIRING,
                        "providerStatus": task.reference.status,
                        "cancellationRequestedAt": iso_now(),
                        "nextReconcileAt": self._next(),
                        "updatedAt": iso_now(),
                    },
                )
            )
        if task.reference.status in TERMINAL_PROVIDER_STATUSES:
            return self._finish_results(meta, task)
        meta, _ = self._sync_journals(meta, execution_id)
        return self._persist_task(meta, task)

    def _mitigation_work(
        self,
        meta: dict[str, Any],
        *,
        execution_id: str,
        summary_record_id: str,
        updated_at: str,
        event_status: str = "COMPLETED",
        result_fetch_started_at: str | None = None,
    ) -> dict[str, Any]:
        now = iso_now()
        return {
            "PK": f"INV#{meta['investigationId']}",
            "SK": f"MITIGATION#{execution_id}",
            "investigationId": meta["investigationId"],
            "workflowState": MITIGATION_FETCHING,
            "nextReconcileAt": now,
            "mitigationExecutionId": execution_id,
            "mitigationSummaryRecordId": summary_record_id,
            "mitigationUpdatedAt": updated_at,
            "mitigationEventStatus": event_status,
            "resultFetchStartedAt": result_fetch_started_at or now,
            "attemptCount": 0,
            "createdAt": now,
            "updatedAt": now,
        }

    def _defer_mitigation_work(
        self, work: dict[str, Any], error_type: str
    ) -> dict[str, Any]:
        deferred = {
            **work,
            "nextReconcileAt": self._next(),
            "attemptCount": int(work.get("attemptCount", 0)) + 1,
            "lastErrorType": error_type,
            "updatedAt": iso_now(),
        }
        persisted = self.store.put_mitigation_work(deferred, expected=work)
        logger.warning(
            json.dumps(
                {
                    "_aws": {
                        "Timestamp": int(utc_now().timestamp() * 1000),
                        "CloudWatchMetrics": [
                            {
                                "Namespace": "CloudOps/DevOpsAgent",
                                "Dimensions": [["Operation"]],
                                "Metrics": [
                                    {
                                        "Name": "MitigationReadErrors",
                                        "Unit": "Count",
                                    }
                                ],
                            }
                        ],
                    },
                    "Operation": "FetchMitigationResults",
                    "MitigationReadErrors": 1,
                    "errorType": error_type,
                }
            )
        )
        return persisted or deferred

    def _finish_mitigation_unavailable(
        self, meta: dict[str, Any], work: dict[str, Any]
    ) -> dict[str, Any]:
        completion_reported = work.get("mitigationEventStatus") == "COMPLETED"
        mitigation = {
            "status": (
                "RESULTS_UNAVAILABLE"
                if completion_reported
                else "STATUS_UPDATE_UNAVAILABLE"
            ),
            "terminal": True,
            "action": (
                "Mitigation result unavailable"
                if completion_reported
                else "Mitigation status update unavailable"
            ),
            "description": (
                (
                    "AWS DevOps Agent reported mitigation completion, but its "
                    "summary could not be retrieved."
                )
                if completion_reported
                else (
                    "No terminal mitigation event or summary was received "
                    "before reconciliation expired."
                )
            ),
            "updatedAt": work.get("mitigationUpdatedAt") or iso_now(),
        }
        outcome = redact(merge_outcome(meta.get("outcome"), mitigation=mitigation))
        updated = self.store.update_meta(
            meta["investigationId"],
            {
                "projectionVersion": 1,
                "outcome": outcome,
                "mitigationStatus": mitigation["status"],
                "mitigationTerminal": True,
                "mitigationUpdatedAt": mitigation["updatedAt"],
                "updatedAt": iso_now(),
            },
        )
        self.store.delete_mitigation_work(work)
        return _json_safe(updated)

    def _reconcile_mitigation_work(self, work: dict[str, Any]) -> dict[str, Any]:
        meta = self.store.get_meta(work["investigationId"])
        if not meta:
            self.store.delete_mitigation_work(work)
            return {"ignored": True, "reason": "INVESTIGATION_NOT_FOUND"}

        execution_id = str(work.get("mitigationExecutionId", ""))
        summary_record_id = str(work.get("mitigationSummaryRecordId", ""))
        if self._mitigation_work_is_stale(meta, work):
            self.store.delete_mitigation_work(work)
            return _json_safe(meta)

        if self._fetch_deadline_expired(str(work.get("resultFetchStartedAt", ""))):
            meta = self.store.get_meta(work["investigationId"])
            if not meta:
                self.store.delete_mitigation_work(work)
                return {"ignored": True, "reason": "INVESTIGATION_NOT_FOUND"}
            if self._mitigation_work_is_stale(meta, work):
                self.store.delete_mitigation_work(work)
                return _json_safe(meta)
            return self._finish_mitigation_unavailable(meta, work)

        try:
            records = self.provider.list_journal_records(execution_id)
            summary_record = next(
                (
                    record
                    for record in reversed(records)
                    if record.get("recordType") == "mitigation_summary_md"
                    and (
                        not execution_id
                        or not record.get("executionId")
                        or record.get("executionId") == execution_id
                    )
                    and (
                        not summary_record_id
                        or record.get("recordId") == summary_record_id
                    )
                ),
                None,
            )
            if summary_record and not summary_record_id:
                summary_record_id = str(summary_record.get("recordId", ""))
            mitigation = project_mitigation(
                records,
                execution_id=execution_id,
                summary_record_id=summary_record_id,
                status="COMPLETED",
                updated_at=str(work.get("mitigationUpdatedAt") or iso_now()),
            )
        except Exception as exc:
            self._defer_mitigation_work(work, type(exc).__name__)
            return _json_safe(meta)

        if mitigation is None:
            self._defer_mitigation_work(work, "SummaryNotAvailable")
            return _json_safe(meta)

        meta = self.store.get_meta(work["investigationId"])
        if not meta:
            self.store.delete_mitigation_work(work)
            return {"ignored": True, "reason": "INVESTIGATION_NOT_FOUND"}
        if self._mitigation_work_is_stale(meta, work):
            self.store.delete_mitigation_work(work)
            return _json_safe(meta)

        outcome = redact(merge_outcome(meta.get("outcome"), mitigation=mitigation))
        updated = self.store.update_meta(
            meta["investigationId"],
            {
                "projectionVersion": 1,
                "outcome": outcome,
                "mitigationStatus": mitigation["status"],
                "mitigationTerminal": True,
                "mitigationExecutionId": execution_id,
                "mitigationSummaryRecordId": summary_record_id,
                "mitigationUpdatedAt": mitigation["updatedAt"],
                "updatedAt": iso_now(),
            },
        )
        self.store.delete_mitigation_work(work)
        return _json_safe(updated)

    def _handle_mitigation_event(
        self,
        meta: dict[str, Any],
        detail_type: str,
        metadata: dict[str, Any],
        data: dict[str, Any],
    ) -> dict[str, Any]:
        status = str(data.get("status", "")).upper()
        if not status:
            status = detail_type.removeprefix("Mitigation ").replace(" ", "_").upper()
        if status == "CANCELLED":
            status = "CANCELED"
        execution_id = str(metadata.get("execution_id", ""))
        summary_record_id = str(data.get("summary_record_id", ""))
        updated_at = self._provider_time(data.get("updated_at"))

        current_updated_at = str(meta.get("mitigationUpdatedAt", ""))
        if self._time_is_after(current_updated_at, updated_at):
            return _json_safe(meta)
        if current_updated_at == updated_at and meta.get("mitigationTerminal"):
            return _json_safe(meta)

        if status in {"IN_PROGRESS", "COMPLETED"} and execution_id:
            if status == "COMPLETED" and not summary_record_id:
                raise ValueError(
                    "Completed mitigation event is missing execution_id or summary_record_id"
                )
            existing = self.store.get_mitigation_work(
                meta["investigationId"], execution_id
            )
            work = self._mitigation_work(
                meta,
                execution_id=execution_id,
                summary_record_id=summary_record_id,
                updated_at=updated_at,
                event_status=status,
                result_fetch_started_at=(
                    str(existing.get("resultFetchStartedAt"))
                    if existing and existing.get("mitigationEventStatus") == status
                    else None
                ),
            )
            self.store.put_mitigation_work(work)
            mitigation = {
                "status": status,
                "terminal": False,
                "action": "",
                "description": "",
                "updatedAt": updated_at,
            }
            meta = self.store.update_meta(
                meta["investigationId"],
                {
                    "projectionVersion": 1,
                    "outcome": redact(
                        merge_outcome(meta.get("outcome"), mitigation=mitigation)
                    ),
                    "mitigationStatus": (
                        MITIGATION_FETCHING if status == "COMPLETED" else status
                    ),
                    "mitigationTerminal": False,
                    "mitigationExecutionId": execution_id,
                    "mitigationSummaryRecordId": summary_record_id,
                    "mitigationUpdatedAt": updated_at,
                    "updatedAt": iso_now(),
                },
            )
            return self._reconcile_mitigation_work(work)
        if status == "COMPLETED":
            raise ValueError("Completed mitigation event is missing execution_id")

        mitigation = {
            "status": status,
            "terminal": status in {"FAILED", "TIMED_OUT", "CANCELED"},
            "action": "",
            "description": "",
            "updatedAt": updated_at,
        }
        outcome = redact(
            merge_outcome(
                meta.get("outcome"),
                mitigation=mitigation,
            )
        )
        values = {
            "projectionVersion": 1,
            "outcome": outcome,
            "mitigationStatus": status,
            "mitigationTerminal": bool(mitigation["terminal"]),
            "mitigationExecutionId": execution_id,
            "mitigationUpdatedAt": updated_at,
            "updatedAt": iso_now(),
        }
        if summary_record_id:
            values["mitigationSummaryRecordId"] = summary_record_id
        updated = self.store.update_meta(
            meta["investigationId"],
            values,
        )
        if execution_id:
            existing = self.store.get_mitigation_work(
                meta["investigationId"], execution_id
            )
            if existing:
                self.store.delete_mitigation_work(existing)
        return _json_safe(updated)

    def handle_provider_event(self, event: dict[str, Any]) -> dict[str, Any]:
        event_id = event.get("id", "")
        ttl = int((utc_now() + timedelta(days=7)).timestamp())
        if event_id and not self.store.mark_event(event_id, ttl):
            return {"duplicate": True, "eventId": event_id}
        try:
            detail = event.get("detail", {})
            metadata = detail.get("metadata", {})
            data = detail.get("data", {})
            task_id = metadata.get("task_id")
            if not task_id:
                return {"ignored": True, "reason": "MISSING_TASK_ID"}
            meta = self.store.find_by_task(task_id)
            if not meta:
                if event.get("detail-type") != "Investigation Linked":
                    return {"ignored": True, "reason": "UNKNOWN_TASK"}
                linked_task = self.provider.get_task(task_id)
                primary_task_id = linked_task.reference.primary_task_id
                if not primary_task_id:
                    return {"ignored": True, "reason": "LINKED_WITHOUT_PRIMARY"}
                known_primary = self.store.find_by_task(primary_task_id)
                if known_primary:
                    return _json_safe(known_primary)
                primary_task = self.provider.get_task(primary_task_id)
                return self._import_provider_task(primary_task, linked_task_id=task_id)
            detail_type = str(event.get("detail-type", ""))
            if detail_type.startswith("Mitigation "):
                return self._handle_mitigation_event(meta, detail_type, metadata, data)
            event_status = data.get("status", "")
            if event_status == "CANCELLED":
                event_status = "CANCELED"
            summary_record_id = data.get("summary_record_id")
            task = self.provider.get_task(task_id)
            if task.reference.status == "LINKED":
                return self.reconcile(meta)
            event_values = {
                "providerEventStatus": event_status,
                "updatedAt": iso_now(),
            }
            if summary_record_id:
                event_values["summaryRecordId"] = summary_record_id
            meta = self.store.update_meta(meta["investigationId"], event_values)
            if task.reference.status not in TERMINAL_PROVIDER_STATUSES:
                return self._persist_task(meta, task)
            return self._finish_results(meta, task, summary_record_id=summary_record_id)
        except Exception:
            if event_id:
                self.store.unmark_event(event_id)
            raise

    def _import_provider_task(
        self, task: ProviderTask, *, linked_task_id: str
    ) -> dict[str, Any]:
        """Persist and reconcile a primary task discovered through LINKED."""
        investigation_id = str(uuid.uuid4())
        source = f"DEVOPS_AGENT#{task.reference.task_id}"
        now = iso_now()
        state = self._workflow_state(task.reference.status)
        meta = {
            "PK": f"INV#{investigation_id}",
            "SK": "META",
            "investigationId": investigation_id,
            "source": "DEVOPS_AGENT",
            "sourceKey": source,
            "automatic": False,
            "workflowState": state,
            "providerStatus": task.reference.status,
            "providerTaskId": task.reference.task_id,
            "providerExecutionId": task.reference.execution_id or "",
            "providerStartedAt": now,
            "linkedTaskId": linked_task_id,
            "createdAt": now,
            "updatedAt": now,
            "nextReconcileAt": now,
            "leaseHeld": False,
        }
        pointer = {
            "PK": f"SOURCE#{source_hash(source)}",
            "SK": "POINTER",
            "sourceKey": source,
            "investigationId": investigation_id,
            "createdAt": now,
            "updatedAt": now,
        }
        if not self.store.claim_source(meta, pointer):
            winner = self.store.get_pointer(source)
            return _json_safe(self.store.get_meta(winner["investigationId"]))
        if task.reference.status in TERMINAL_PROVIDER_STATUSES:
            return self._finish_results(meta, task)
        return _json_safe(meta)

    def reconcile_due(self, limit: int = 100) -> list[dict[str, Any]]:
        now = utc_now()
        slot = int(now.timestamp()) // max(1, self.config.reconciliation_seconds)
        offset = slot % len(DUE_STATES)
        states = DUE_STATES[offset:] + DUE_STATES[:offset]
        results = []
        failures = 0
        for meta in self.store.due(now.isoformat(), limit=limit, states=states):
            try:
                if str(meta.get("SK", "")).startswith("MITIGATION#"):
                    results.append(self._reconcile_mitigation_work(meta))
                else:
                    results.append(self.reconcile(meta))
            except Exception as exc:
                failures += 1
                logger.exception(
                    "DevOps Agent reconciliation failed",
                    extra={
                        "investigationId": meta.get("investigationId", ""),
                        "workType": (
                            "mitigation"
                            if str(meta.get("SK", "")).startswith("MITIGATION#")
                            else "investigation"
                        ),
                    },
                )
                if str(meta.get("SK", "")).startswith("MITIGATION#"):
                    self._defer_mitigation_work(meta, type(exc).__name__)
                else:
                    self.store.update_meta(
                        meta["investigationId"],
                        {
                            "lastErrorType": type(exc).__name__,
                            "nextReconcileAt": self._next(),
                            "updatedAt": iso_now(),
                        },
                    )
        if failures:
            logger.error(
                json.dumps(
                    {
                        "_aws": {
                            "Timestamp": int(utc_now().timestamp() * 1000),
                            "CloudWatchMetrics": [
                                {
                                    "Namespace": "CloudOps/DevOpsAgent",
                                    "Dimensions": [],
                                    "Metrics": [
                                        {
                                            "Name": "ReconciliationErrors",
                                            "Unit": "Count",
                                        }
                                    ],
                                }
                            ],
                        },
                        "ReconciliationErrors": failures,
                    }
                )
            )
            raise RuntimeError(f"{failures} DevOps Agent reconciliation item(s) failed")
        return results

    def get_health(
        self, event_arn: str, account_id: str, *, include_journal: bool = False
    ) -> dict[str, Any]:
        pointer = self.store.get_pointer(source_key(event_arn, account_id))
        if not pointer:
            return {"found": False}
        meta = self.store.get_meta(pointer["investigationId"])
        if not meta:
            return {"found": False}
        response = {"found": True, "investigation": _json_safe(meta)}
        if include_journal:
            response["activity"] = _json_safe(meta.get("activity", []))
        return response


def build_coordinator() -> InvestigationCoordinator:
    config = CoordinatorConfig.from_env()
    table_name = os.environ["INVESTIGATIONS_TABLE_NAME"]
    provider = DevOpsAgentProvider(
        config.agent_space_id,
        os.environ.get(
            "DEVOPS_AGENT_SPACE_REGION",
            os.environ.get("AWS_REGION", "us-east-1"),
        ),
        coverage_cache_ttl=int(
            os.environ.get("DEVOPS_AGENT_COVERAGE_CACHE_TTL_SECONDS", "300")
        ),
    )
    return InvestigationCoordinator(InvestigationStore(table_name), provider, config)
