"""Lambda entry points for the DevOps Agent investigation workflow."""

from __future__ import annotations

import logging

from boto3.dynamodb.types import TypeDeserializer

from shared.devops_agent_coordinator import build_coordinator

logger = logging.getLogger()
logger.setLevel("INFO")
_deserializer = TypeDeserializer()
_coordinator = None


def _get_coordinator():
    global _coordinator
    if _coordinator is None:
        _coordinator = build_coordinator()
    return _coordinator


def _deserialize(image):
    return {key: _deserializer.deserialize(value) for key, value in image.items()}


def stream_handler(event, context):
    """Start eligible investigations from Health table stream records."""
    failures = []
    coordinator = _get_coordinator()
    for record in event.get("Records", []):
        sequence = record.get("dynamodb", {}).get("SequenceNumber", "")
        try:
            if record.get("eventName") not in {"INSERT", "MODIFY"}:
                continue
            image = record.get("dynamodb", {}).get("NewImage")
            if image:
                coordinator.request_health(_deserialize(image), automatic=True)
        except Exception:
            logger.exception("Health stream record failed")
            failures.append({"itemIdentifier": sequence})
    return {"batchItemFailures": failures}


def event_handler(event, context):
    """Reconcile a DevOps Agent EventBridge task event."""
    detail = event.get("detail", {})
    metadata = detail.get("metadata", {})
    logger.info(
        "DevOps Agent event received",
        extra={
            "eventId": event.get("id", ""),
            "detailType": event.get("detail-type", ""),
            "taskId": metadata.get("task_id", ""),
            "requestId": getattr(context, "aws_request_id", ""),
        },
    )
    return _get_coordinator().handle_provider_event(event)


def reconcile_handler(event, context):
    """Reconcile one requested investigation or all due workflow work."""
    investigation_id = str(event.get("investigationId", ""))
    coordinator = _get_coordinator()
    if investigation_id:
        return {"investigation": coordinator.refresh(investigation_id)}
    results = coordinator.reconcile_due()
    return {"reconciled": len(results)}
