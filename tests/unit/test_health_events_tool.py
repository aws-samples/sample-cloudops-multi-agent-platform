from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "src" / "lambda" / "mcp" / "health-events" / "handler.py"
SPEC = importlib.util.spec_from_file_location("health_events_tool_handler", PATH)
health_events = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(health_events)


def test_recent_inventory_includes_old_active_and_only_recent_closed(monkeypatch):
    cutoff = "2026-08-22T00:00:00"
    old_open = {
        "eventArn": "arn:open",
        "accountId": "111111111111",
        "eventTypeCategory": "scheduledChange",
        "statusCode": "open",
        "lastUpdateTime": "2026-08-01T00:00:00",
    }
    old_upcoming = {
        "eventArn": "arn:upcoming",
        "accountId": "111111111111",
        "eventTypeCategory": "scheduledChange",
        "statusCode": "upcoming",
        "lastUpdateTime": "2026-07-01T00:00:00",
    }
    recent_closed = {
        "eventArn": "arn:recent-closed",
        "accountId": "111111111111",
        "eventTypeCategory": "issue",
        "statusCode": "closed",
        "lastUpdateTime": "2026-08-28T00:00:00",
    }
    old_closed = {
        "eventArn": "arn:old-closed",
        "accountId": "111111111111",
        "eventTypeCategory": "issue",
        "statusCode": "closed",
        "lastUpdateTime": "2026-08-01T00:00:00",
    }
    source = [old_open, old_open.copy(), old_upcoming, recent_closed, old_closed]
    calls = []

    def values(condition):
        expression = condition.get_expression()
        result = []
        for value in expression["values"]:
            if hasattr(value, "get_expression"):
                result.extend(values(value))
            elif isinstance(value, str):
                result.append(value)
        return result

    def fake_query(index_name, key_cond, filter_expr=None, limit=200, reverse=True):
        del limit, reverse
        key_values = values(key_cond)
        calls.append((index_name, key_values))
        assert index_name == health_events._STATUS_INDEX
        assert filter_expr is None
        status = key_values[0]
        query_cutoff = key_values[1] if len(key_values) > 1 else None
        return [
            item
            for item in source
            if item["statusCode"] == status
            and (query_cutoff is None or item["lastUpdateTime"] >= query_cutoff)
        ]

    monkeypatch.setattr(health_events, "_date_n_days_ago", lambda _: cutoff)
    monkeypatch.setattr(health_events, "_query", fake_query)

    result = health_events.handle_get_recent_events({"days_back": 7})

    assert result["includes_all_active"] is True
    assert result["count"] == 3
    assert [item["eventArn"] for item in result["events"]] == [
        "arn:open",
        "arn:upcoming",
        "arn:recent-closed",
    ]
    assert all(
        cutoff not in key_values
        for _, key_values in calls
        if key_values[0] in health_events._ACTIVE_STATUSES
    )
    assert cutoff in calls[-1][1]
