"""AWS Health API — scheduled Direct Connect maintenance events.

Python port of source ``dx-visualizer/src/api/health-dx.ts``.

Requires Business/Enterprise On-Ramp/Enterprise Support. Accounts without a
qualifying plan get ``SubscriptionRequiredException`` — we swallow that and
return empty so the UI hides the calendar feature.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from typing import Any, Dict, List

from ..types import DxMaintenanceEvent
from . import clients

logger = logging.getLogger(__name__)

_SUBSCRIPTION_RE = re.compile(r"SubscriptionRequired", re.IGNORECASE)


def fetch_dx_maintenance_events() -> List[DxMaintenanceEvent]:
    try:
        client = clients.health()
        events_res = client.describe_events(
            filter={
                "services": ["DIRECTCONNECT"],
                "eventTypeCategories": ["scheduledChange"],
                "eventStatusCodes": ["upcoming", "open"],
            },
            maxResults=50,
        )
    except Exception as err:  # noqa: BLE001
        msg = str(err)
        if _SUBSCRIPTION_RE.search(msg):
            logger.info(
                "[AWS] Health: skipping (requires Business/Enterprise support)"
            )
        else:
            logger.warning("[AWS] Health: fetch failed: %s", msg)
        return []

    events = events_res.get("events") or []
    if not events:
        logger.info("[AWS] Health: no Direct Connect maintenance events")
        return []

    event_arns = [e.get("arn") for e in events if e.get("arn")]

    # Fetch descriptions and affected entity IDs. Source did these in parallel;
    # within one Lambda invocation the sequential cost is negligible.
    try:
        details_res = client.describe_event_details(eventArns=event_arns)
    except Exception as err:  # noqa: BLE001
        logger.warning("[AWS] Health: describe_event_details failed: %s", err)
        details_res = {"successfulSet": []}

    try:
        entities_res = client.describe_affected_entities(
            filter={"eventArns": event_arns}
        )
    except Exception as err:  # noqa: BLE001
        logger.warning(
            "[AWS] Health: describe_affected_entities failed: %s", err
        )
        entities_res = {"entities": []}

    description_by_arn: Dict[str, str] = {}
    for d in details_res.get("successfulSet") or []:
        arn = (d.get("event") or {}).get("arn")
        desc = (d.get("eventDescription") or {}).get("latestDescription")
        if arn and desc:
            description_by_arn[arn] = desc

    entities_by_arn: Dict[str, List[str]] = {}
    for ent in entities_res.get("entities") or []:
        arn = ent.get("eventArn")
        val = ent.get("entityValue")
        if not arn or not val:
            continue
        entities_by_arn.setdefault(arn, []).append(val)

    result: List[DxMaintenanceEvent] = []
    for e in events:
        arn = e.get("arn")
        if not arn:
            continue
        result.append(
            {
                "arn": arn,
                "eventTypeCode": e.get("eventTypeCode", ""),
                "region": e.get("region", ""),
                "startTime": _iso(e.get("startTime")),
                "endTime": _iso(e.get("endTime")),
                "lastUpdatedTime": _iso(e.get("lastUpdatedTime")),
                "statusCode": e.get("statusCode", ""),
                "affectedResourceIds": entities_by_arn.get(arn, []),
                "description": description_by_arn.get(arn, ""),
            }
        )

    logger.info(
        "[AWS] Health: %d Direct Connect maintenance event(s)", len(result)
    )
    return result


def _iso(dt: Any) -> str | None:
    """boto3 returns datetime objects for Health timestamps. Source
    re-serializes to ISO 8601."""
    if dt is None:
        return None
    if isinstance(dt, _dt.datetime):
        return dt.isoformat()
    return str(dt)
