from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts" / "backfill_health.py"
SPEC = importlib.util.spec_from_file_location("health_backfill", PATH)
backfill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backfill)
# The script adds the collector directory so its deferred `import handler`
# works when run directly. Do not leak that top-level module resolution into
# unrelated unit tests.
COLLECTOR_DIR = str(
    ROOT / "src" / "lambda" / "collectors" / "health-events"
)
while COLLECTOR_DIR in sys.path:
    sys.path.remove(COLLECTOR_DIR)


class Pages:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **kwargs):
        return iter(self.pages)


def test_organization_affected_accounts_are_paginated():
    health = MagicMock()
    health.get_paginator.return_value = Pages(
        [
            {"affectedAccounts": ["111111111111"]},
            {"affectedAccounts": ["222222222222", "111111111111"]},
        ]
    )

    result = backfill._affected_accounts_for_event(health, "event-arn")

    assert result == ["111111111111", "222222222222"]


def test_org_payload_uses_affected_account_not_entity_account_id():
    health = MagicMock()
    health.describe_event_details_for_organization.return_value = {
        "successfulSet": [
            {"eventDescription": {"latestDescription": "maintenance"}}
        ]
    }
    health.get_paginator.return_value = Pages(
        [{"entities": [{"entityValue": "i-123", "awsAccountId": "wrong"}]}]
    )
    event = {
        "arn": "arn:aws:health:us-east-1::event/EC2/TEST/1",
        "service": "EC2",
        "eventTypeCode": "TEST",
        "eventTypeCategory": "scheduledChange",
        "eventScopeCode": "ACCOUNT_SPECIFIC",
        "statusCode": "upcoming",
        "actionability": "ACTION_REQUIRED",
        "personas": ["OPERATIONS"],
        "region": "us-east-1",
    }

    detail = backfill._enrich_event_details(
        health, event, True, "111111111111"
    )

    assert detail["affectedAccount"] == "111111111111"
    assert detail["actionability"] == "ACTION_REQUIRED"
    assert detail["personas"] == ["OPERATIONS"]
    assert detail["affectedEntities"] == [{"entityValue": "i-123"}]
