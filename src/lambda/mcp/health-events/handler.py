"""
AWS Health Events MCP Server - Lambda Implementation for AgentCore Gateway

Queries AWS Health events stored in DynamoDB. Data is populated by the
health events collector (deployed separately via terraform/modules/custom/
health-events-collection/).

Access patterns and index strategy:
    Table PK/SK:        (eventArn, accountId)     — point lookup by ARN
    GSI CategoryTimeIndex: (eventTypeCategory, lastUpdateTime)
    GSI AccountTimeIndex:  (accountId, lastUpdateTime)

Query routing:
    by ARN                  -> GetItem on base table
    by account + time       -> Query AccountTimeIndex
    by category + time      -> Query CategoryTimeIndex (used for critical/recent)
    by service-only         -> Scan (no suitable index; service cardinality is low)
    arbitrary multi-filter  -> best-fit index then FilterExpression for the rest

Tools (7):
- get_health_events: flexible multi-filter query
- get_events_by_account: per-account query (uses AccountTimeIndex)
- get_events_by_service: per-service query (scan; service is not a key)
- get_critical_events: CRITICAL/HIGH in time window (uses CategoryTimeIndex)
- get_recent_events: events in time window (uses CategoryTimeIndex across cats)
- get_event_summary: grouped counts
- get_event_by_arn: single event lookup

Required IAM Permissions:
- dynamodb:Scan, dynamodb:Query, dynamodb:GetItem
"""

import decimal
import json
import os
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Attr, Key

DYNAMODB_TABLE_NAME = os.environ.get("HEALTH_EVENTS_TABLE_NAME", "")
DYNAMODB_REGION = os.environ.get("AWS_REGION", "us-east-1")

_ACCOUNT_INDEX = "AccountTimeIndex"
_CATEGORY_INDEX = "CategoryTimeIndex"
_EVENT_CATEGORIES = ("issue", "scheduledChange", "accountNotification", "investigation")


def _get_table():
    return boto3.resource("dynamodb", region_name=DYNAMODB_REGION).Table(
        DYNAMODB_TABLE_NAME
    )


def handler(event, context):
    print(f"Event: {json.dumps(event, default=str)}")
    extended_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
    tool_name = extended_tool_name.split("___")[1]
    print(f"Tool: {tool_name}")

    if not DYNAMODB_TABLE_NAME:
        return {"error": "HEALTH_EVENTS_TABLE_NAME not configured"}

    handlers = {
        "get_health_events": handle_get_health_events,
        "get_events_by_account": handle_get_events_by_account,
        "get_events_by_service": handle_get_events_by_service,
        "get_critical_events": handle_get_critical_events,
        "get_recent_events": handle_get_recent_events,
        "get_event_summary": handle_get_event_summary,
        "get_event_by_arn": handle_get_event_by_arn,
    }
    fn = handlers.get(tool_name)
    if fn:
        resp = fn(event)
        print(
            f"Response keys: {list(resp.keys()) if isinstance(resp, dict) else 'n/a'}"
        )
        return resp
    return {
        "error": f"Unknown tool: {tool_name}",
        "available_tools": list(handlers.keys()),
    }


# ---------------------------------------------------------------------------
# Time / serialization helpers
# ---------------------------------------------------------------------------
def _date_n_days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


def _serialize(items):
    return json.loads(
        json.dumps(
            items,
            default=lambda o: float(o) if isinstance(o, decimal.Decimal) else str(o),
        )
    )


# ---------------------------------------------------------------------------
# Query / scan primitives
# ---------------------------------------------------------------------------
def _query(index_name, key_cond, filter_expr=None, limit=200, reverse=True):
    """Query a GSI with optional post-filter. Paginates up to `limit` matches.

    reverse=True returns newest-first on time-range queries (range key
    scan order is descending).
    """
    table = _get_table()
    items = []
    kwargs = {
        "IndexName": index_name,
        "KeyConditionExpression": key_cond,
        "Limit": 1000,
        "ScanIndexForward": not reverse,
    }
    if filter_expr is not None:
        kwargs["FilterExpression"] = filter_expr

    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if len(items) >= limit or "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return _serialize(items[:limit])


def _scan_with_filter(filter_expr, limit=200):
    """Fallback scan for access patterns not covered by any GSI."""
    table = _get_table()
    items = []
    kwargs = {"FilterExpression": filter_expr, "Limit": 1000}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if len(items) >= limit or "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return _serialize(items[:limit])


def _scan_all(limit=200):
    table = _get_table()
    items = []
    kwargs = {"Limit": 1000}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if len(items) >= limit or "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return _serialize(items[:limit])


def _resolve_time_window(event):
    """Return (start_iso, end_iso_or_None) from days_back OR start/end_date.

    days_back wins if both are provided. Returns (None, None) if neither is
    supplied (caller decides whether to add a default).
    """
    days_back = event.get("days_back")
    if days_back:
        return _date_n_days_ago(int(days_back)), None
    start = event.get("start_date")
    end = event.get("end_date")
    if start or end:
        return start, end
    return None, None


def _time_range_key(sk_name, start, end):
    """Build a KeyConditionExpression fragment for the range key."""
    if start and end:
        return Key(sk_name).between(start, end)
    if start:
        return Key(sk_name).gte(start)
    if end:
        return Key(sk_name).lte(end)
    return None


# ---------------------------------------------------------------------------
# Filter builders (used after Query when we still need to narrow results)
# ---------------------------------------------------------------------------
def _build_post_filter(event, *, exclude=()):
    """Compose an optional post-query filter from the non-key fields.

    Caller tells us which fields are already pinned by the Query key (exclude
    those to avoid redundant FilterExpressions). Returns None if nothing to
    filter on.
    """
    conditions = []
    mapping = [
        ("account_id", "accountId"),
        ("service", "service"),
        ("status", "statusCode"),
        ("region", "region"),
        ("risk_level", "riskLevel"),
        ("event_scope", "eventScopeCode"),
    ]
    for key, attr in mapping:
        if key in exclude:
            continue
        val = event.get(key)
        if val:
            conditions.append(Attr(attr).eq(val.upper() if key == "risk_level" else val))
    if not conditions:
        return None
    expr = conditions[0]
    for c in conditions[1:]:
        expr = expr & c
    return expr


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------
def handle_get_health_events(event):
    """Flexible multi-filter query — picks the best index for the filters given.

    Routing:
      account_id + (days_back|start_date|end_date) -> AccountTimeIndex
      (days_back|start_date|end_date) alone        -> CategoryTimeIndex per cat
      account_id alone                             -> AccountTimeIndex
      other filters only                           -> Scan
    """
    try:
        start, end = _resolve_time_window(event)
        account_id = event.get("account_id")
        limit = int(event.get("limit", 100))

        if account_id:
            key_cond = Key("accountId").eq(str(account_id))
            tr = _time_range_key("lastUpdateTime", start, end)
            if tr is not None:
                key_cond = key_cond & tr
            filt = _build_post_filter(event, exclude=("account_id",))
            items = _query(_ACCOUNT_INDEX, key_cond, filt, limit)
            return {"count": len(items), "events": items}

        if start or end:
            # No account pinned; query each category partition on the time SK
            # and merge. Four small queries beat a full table scan.
            all_items = []
            filt = _build_post_filter(event)
            for cat in _EVENT_CATEGORIES:
                key_cond = Key("eventTypeCategory").eq(cat)
                tr = _time_range_key("lastUpdateTime", start, end)
                if tr is not None:
                    key_cond = key_cond & tr
                all_items.extend(_query(_CATEGORY_INDEX, key_cond, filt, limit))
                if len(all_items) >= limit:
                    break
            all_items.sort(key=lambda x: x.get("lastUpdateTime", ""), reverse=True)
            return {"count": len(all_items[:limit]), "events": all_items[:limit]}

        # No account + no time — scan with whatever filters remain
        filt = _build_post_filter(event)
        if filt is not None:
            items = _scan_with_filter(filt, limit)
        else:
            items = _scan_all(limit)
        return {"count": len(items), "events": items}
    except Exception as e:
        return {"error": str(e)}


def handle_get_events_by_account(event):
    account_id = event.get("account_id")
    if not account_id:
        return {"error": "account_id is required"}
    try:
        key_cond = Key("accountId").eq(str(account_id))
        start, end = _resolve_time_window(event)
        tr = _time_range_key("lastUpdateTime", start, end)
        if tr is not None:
            key_cond = key_cond & tr
        items = _query(
            _ACCOUNT_INDEX,
            key_cond,
            filter_expr=None,
            limit=int(event.get("limit", 100)),
        )
        return {"account_id": account_id, "count": len(items), "events": items}
    except Exception as e:
        return {"error": str(e)}


def handle_get_events_by_service(event):
    """Service is not a key attribute — stays as a filtered scan.

    Adding a ServiceTimeIndex GSI would help if this becomes a hot path,
    but service has low cardinality (~20 values across the whole org) so
    a scan with filter is acceptable.
    """
    service = event.get("service")
    if not service:
        return {"error": "service is required"}
    try:
        cond = Attr("service").eq(service)
        start, end = _resolve_time_window(event)
        if start:
            cond = cond & Attr("lastUpdateTime").gte(start)
        if end:
            cond = cond & Attr("lastUpdateTime").lte(end)
        if event.get("status"):
            cond = cond & Attr("statusCode").eq(event["status"])
        items = _scan_with_filter(cond, int(event.get("limit", 100)))
        return {"service": service, "count": len(items), "events": items}
    except Exception as e:
        return {"error": str(e)}


def handle_get_critical_events(event):
    """CRITICAL/HIGH risk in a time window.

    Risk level correlates tightly with eventTypeCategory=issue (risk rules
    escalate open issues). Query just that partition on CategoryTimeIndex,
    then filter on riskLevel. Cuts read volume vs scanning the whole table.
    """
    try:
        days_back = int(event.get("days_back", 30))
        start = _date_n_days_ago(days_back)
        limit = int(event.get("limit", 100))

        key_cond = Key("eventTypeCategory").eq("issue") & Key("lastUpdateTime").gte(start)
        filt = Attr("riskLevel").is_in(["CRITICAL", "HIGH"])
        items = _query(_CATEGORY_INDEX, key_cond, filt, limit)
        return {"count": len(items), "days_back": days_back, "events": items}
    except Exception as e:
        return {"error": str(e)}


def handle_get_recent_events(event):
    """All events in a time window, merged across categories."""
    try:
        days_back = int(event.get("days_back", 7))
        start = _date_n_days_ago(days_back)
        limit = int(event.get("limit", 100))

        filt = Attr("statusCode").eq(event["status"]) if event.get("status") else None

        all_items = []
        for cat in _EVENT_CATEGORIES:
            key_cond = Key("eventTypeCategory").eq(cat) & Key("lastUpdateTime").gte(start)
            all_items.extend(_query(_CATEGORY_INDEX, key_cond, filt, limit))
            if len(all_items) >= limit:
                break
        all_items.sort(key=lambda x: x.get("lastUpdateTime", ""), reverse=True)
        return {"days_back": days_back, "count": len(all_items[:limit]), "events": all_items[:limit]}
    except Exception as e:
        return {"error": str(e)}


def handle_get_event_summary(event):
    """Summary counts grouped by a dimension."""
    try:
        group_by = event.get("group_by", "status")
        field_map = {
            "status": "statusCode",
            "service": "service",
            "region": "region",
            "risk_level": "riskLevel",
            "event_scope": "eventScopeCode",
            "category": "eventTypeCategory",
        }
        field = field_map.get(group_by, "statusCode")

        days_back = event.get("days_back")
        if days_back:
            # Time-bounded summary — sum across category partitions
            start = _date_n_days_ago(int(days_back))
            items = []
            for cat in _EVENT_CATEGORIES:
                key_cond = Key("eventTypeCategory").eq(cat) & Key("lastUpdateTime").gte(start)
                items.extend(_query(_CATEGORY_INDEX, key_cond, None, 1000))
        else:
            items = _scan_all(1000)

        counts = {}
        for item in items:
            key = item.get(field, "unknown")
            counts[key] = counts.get(key, 0) + 1
        summary = sorted(
            [{"value": k, "count": v} for k, v in counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )
        return {"total_events": len(items), "group_by": group_by, "summary": summary}
    except Exception as e:
        return {"error": str(e)}


def handle_get_event_by_arn(event):
    event_arn = event.get("event_arn")
    if not event_arn:
        return {"error": "event_arn is required"}
    try:
        account_id = event.get("account_id")
        if account_id:
            resp = _get_table().get_item(
                Key={"eventArn": event_arn, "accountId": str(account_id)}
            )
            item = resp.get("Item")
        else:
            # No account_id — we don't know the sort key, so we have to scan.
            # Caller should pass account_id whenever possible.
            items = _scan_with_filter(Attr("eventArn").eq(event_arn), 1)
            item = items[0] if items else None
        if not item:
            return {"event_arn": event_arn, "found": False}
        return {
            "found": True,
            "event": _serialize([item])[0] if isinstance(item, dict) else item,
        }
    except Exception as e:
        return {"error": str(e)}
