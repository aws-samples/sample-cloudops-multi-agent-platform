"""Typed boundary around the AWS DevOps Agent boto3 client."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import (
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)


class AmbiguousCreateError(RuntimeError):
    """The request may have reached the service and must be retried unchanged."""


class ProviderRateLimitError(RuntimeError):
    """The provider rejected work due to a transient rate or quota limit."""


@dataclass(frozen=True)
class TaskReference:
    task_id: str
    execution_id: str | None
    status: str
    primary_task_id: str | None = None


@dataclass(frozen=True)
class ProviderTask:
    reference: TaskReference


def serialize_provider_value(value: Any) -> Any:
    """Return a JSON/DynamoDB-safe faithful copy of a boto response value."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(k): serialize_provider_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize_provider_value(v) for v in value]
    return value


class DevOpsAgentProvider:
    """Provider-neutral facade for one Agent Space in one explicit Region."""

    def __init__(
        self,
        agent_space_id: str,
        region_name: str,
        *,
        client=None,
        coverage_cache_ttl: int = 300,
    ):
        if not agent_space_id:
            raise ValueError("agent_space_id is required")
        if not region_name:
            raise ValueError("region_name is required")
        self.agent_space_id = agent_space_id
        self.region_name = region_name
        self.client = client or boto3.client(
            "devops-agent",
            region_name=region_name,
            config=Config(
                retries={"mode": "adaptive", "total_max_attempts": 6},
                connect_timeout=5,
                read_timeout=30,
            ),
        )
        self.coverage_cache_ttl = coverage_cache_ttl
        self._coverage_cache: tuple[float, frozenset[str]] | None = None

    @staticmethod
    def _task(response: dict[str, Any]) -> ProviderTask:
        task = response["task"]
        return ProviderTask(
            reference=TaskReference(
                task_id=str(task["taskId"]),
                execution_id=(
                    str(task["executionId"]) if task.get("executionId") else None
                ),
                status=str(task["status"]),
                primary_task_id=(
                    str(task["primaryTaskId"]) if task.get("primaryTaskId") else None
                ),
            )
        )

    def create_investigation(self, request: dict[str, Any]) -> ProviderTask:
        provider_request = {
            "agentSpaceId": self.agent_space_id,
            "taskType": "INVESTIGATION",
            "title": request["title"],
            "description": request.get("description", ""),
            "priority": request.get("priority", "HIGH"),
        }
        canonical = json.dumps(provider_request, sort_keys=True, separators=(",", ":"))
        provider_request["clientToken"] = hashlib.sha256(
            (request["investigationId"] + canonical).encode("utf-8")
        ).hexdigest()
        try:
            return self._task(self.client.create_backlog_task(**provider_request))
        except (
            ConnectionClosedError,
            ConnectTimeoutError,
            EndpointConnectionError,
            ReadTimeoutError,
        ) as exc:
            raise AmbiguousCreateError(str(exc)) from exc
        except (
            self.client.exceptions.ThrottlingException,
            self.client.exceptions.ServiceQuotaExceededException,
        ) as exc:
            raise ProviderRateLimitError(str(exc)) from exc

    def get_task(self, task_id: str) -> ProviderTask:
        return self._task(
            self.client.get_backlog_task(
                agentSpaceId=self.agent_space_id, taskId=task_id
            )
        )

    def list_journal_records(self, execution_id: str) -> list[dict[str, Any]]:
        paginator = self.client.get_paginator("list_journal_records")
        records = []
        for page in paginator.paginate(
            agentSpaceId=self.agent_space_id,
            executionId=execution_id,
            PaginationConfig={"PageSize": 100},
        ):
            records.extend(
                serialize_provider_value(record) for record in page.get("records", [])
            )
        return records

    def list_covered_accounts(self, force_refresh: bool = False) -> frozenset[str]:
        now = time.monotonic()
        if (
            not force_refresh
            and self._coverage_cache
            and now - self._coverage_cache[0] < self.coverage_cache_ttl
        ):
            return self._coverage_cache[1]

        accounts = set()
        paginator = self.client.get_paginator("list_associations")
        for page in paginator.paginate(agentSpaceId=self.agent_space_id):
            for association in page.get("associations", []):
                if association.get("status") != "valid":
                    continue
                configuration = association.get("configuration", {})
                for key in ("sourceAws", "aws"):
                    account_id = configuration.get(key, {}).get("accountId")
                    if account_id:
                        accounts.add(str(account_id))
        covered = frozenset(accounts)
        self._coverage_cache = (now, covered)
        return covered

    def cancel(self, task_id: str) -> ProviderTask:
        return self._task(
            self.client.update_backlog_task(
                agentSpaceId=self.agent_space_id,
                taskId=task_id,
                taskStatus="CANCELED",
                clientToken=hashlib.sha256(
                    f"{self.agent_space_id}:{task_id}:CANCELED".encode("utf-8")
                ).hexdigest(),
            )
        )
