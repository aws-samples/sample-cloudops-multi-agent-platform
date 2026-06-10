#!/usr/bin/env python3
"""
Backfill AWS Health events into the CloudOps health-events DynamoDB table.

Usage:
    .venv/bin/python scripts/backfill_health.py --days 30
    .venv/bin/python scripts/backfill_health.py --days 90 --org
    .venv/bin/python scripts/backfill_health.py --days 14 --org --role-arn <arn>

When to run:
  * Right after first deploy — the EventBridge collector only sees events
    published from that moment forward. Backfill pulls the last N days of
    history so the agent has something to query immediately.
  * Any time the collector has been offline or the table was wiped.
  * To populate an additional account's events after enabling org view.

Support plan REQUIREMENTS (AWS enforces, not us):
  * Single-account mode (--days N): Business+ Support required to call
    `health:DescribeEvents`. Falls through with SubscriptionRequiredException
    on Basic/Developer Support.
  * Org-view mode (--org):          Business+ Support required to call
    `health:DescribeEventsForOrganization`. ALSO requires
    `EnableHealthServiceAccessForOrganization` to have been called from the
    management account at least once. Run from the management account OR a
    delegated admin for Health, OR use --role-arn to assume into one.

Cross-account:
  --role-arn ROLE : Assume the given role before calling Health/Organizations.
                    Used when this script runs from an ops account but
                    org-wide visibility lives in the management/delegated-
                    admin account.

This script does NOT require running inside the Lambda — it imports the
collector's processing logic directly so enrichment (Haiku), risk scoring,
and TTL handling are IDENTICAL to real-time ingest.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make the collector module AND the shared helper importable without packaging.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src" / "lambda" / "collectors" / "health-events"))
sys.path.insert(0, str(_REPO_ROOT / "src" / "lambda" / "mcp"))

import boto3
from botocore.exceptions import ClientError

# Reuse the same cross-account helper the MCP Lambdas and the collector use.
# When CROSS_ACCOUNT_ROLE_ARN_HEALTH is set (or --role-arn is passed and
# propagated to that env var), get_aws_client will assume into it for every
# call to Organizations / Health. Otherwise uses local creds.
from shared.cross_account import get_aws_client  # noqa: E402

# Configure logging BEFORE importing handler so its logger picks up the level.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("backfill")


def _load_collector_config():
    """Pull HEALTH_EVENTS_TABLE_NAME from the deployed Lambda if not already set.

    Saves the user from having to figure out the exact table name — we just
    read it off the deployed collector. Requires AWS credentials for the
    deployment account.
    """
    if os.environ.get("HEALTH_EVENTS_TABLE_NAME"):
        return
    try:
        lam = boto3.client("lambda")
        # Project prefix defaults to cloudops; if the user deployed with a
        # different prefix, require them to set HEALTH_EVENTS_TABLE_NAME.
        fn_name = os.environ.get(
            "COLLECTOR_FUNCTION_NAME", "cloudops-health-events-collector"
        )
        cfg = lam.get_function_configuration(FunctionName=fn_name)
        env = cfg.get("Environment", {}).get("Variables", {})
        table = env.get("HEALTH_EVENTS_TABLE_NAME")
        if not table:
            raise RuntimeError(
                f"Collector {fn_name} has no HEALTH_EVENTS_TABLE_NAME env var. "
                "Is the stack deployed? Set HEALTH_EVENTS_TABLE_NAME manually to override."
            )
        os.environ["HEALTH_EVENTS_TABLE_NAME"] = table
        logger.info(f"Using table name from deployed Lambda: {table}")
    except ClientError as e:
        raise RuntimeError(
            f"Failed to read collector config: {e}. "
            "Set HEALTH_EVENTS_TABLE_NAME and run again."
        ) from e


# Deferred import — HEALTH_EVENTS_TABLE_NAME must be set first for the handler
# module to initialise with the right value.
def _import_handler():
    import handler as h  # noqa: E402

    return h


def _configure_role_arn(role_arn: str | None):
    """If --role-arn was passed, populate CROSS_ACCOUNT_ROLE_ARN_HEALTH so the
    shared cross-account helper picks it up. One code path for assume-role:
    the same env var the collector Lambda uses at runtime.
    """
    if role_arn:
        os.environ["CROSS_ACCOUNT_ROLE_ARN_HEALTH"] = role_arn
        logger.info(f"Using CROSS_ACCOUNT_ROLE_ARN_HEALTH={role_arn}")


def _aws_health_client():
    """AWS Health API is global but Boto3 requires a region. us-east-1 is
    the active endpoint per AWS docs; client will fail over if needed."""
    return get_aws_client("health", region_name="us-east-1", role_alias="HEALTH")


def _sts_caller_account() -> str:
    return get_aws_client("sts", role_alias="HEALTH").get_caller_identity()["Account"]


def _describe_events_single_account(health, start_time_utc):
    """Paginate DescribeEvents for the single account the credentials belong to."""
    paginator = health.get_paginator("describe_events")
    filter_ = {"lastUpdatedTimes": [{"from": start_time_utc}]}
    events = []
    for page in paginator.paginate(filter=filter_):
        events.extend(page.get("events", []))
    logger.info(f"DescribeEvents returned {len(events)} events since {start_time_utc}")
    return events


def _describe_events_org(health, start_time_utc):
    """Paginate DescribeEventsForOrganization for all org member accounts."""
    paginator = health.get_paginator("describe_events_for_organization")
    filter_ = {"lastUpdatedTime": [{"from": start_time_utc}]}
    events = []
    for page in paginator.paginate(filter=filter_):
        events.extend(page.get("events", []))
    logger.info(
        f"DescribeEventsForOrganization returned {len(events)} events since {start_time_utc}"
    )
    return events


def _enrich_event_details(health, event, org_mode: bool):
    """Fetch description + affected entities for one event.

    The `events` list only has summary fields — description and affected
    resources come from separate API calls. This mirrors the shape
    EventBridge delivers so `_process_health_event` treats them identically.
    """
    # Normalise arn key: DescribeEvents returns `arn`, EventBridge uses `eventArn`.
    event_arn = event.get("arn") or event.get("eventArn", "")

    detail_filter = (
        {"organizationEventDetailFilters": [{"eventArn": event_arn}]}
        if org_mode
        else {"eventArns": [event_arn]}
    )
    details_method = (
        health.describe_event_details_for_organization
        if org_mode
        else health.describe_event_details
    )
    try:
        details_resp = details_method(**detail_filter) if org_mode else details_method(
            eventArns=[event_arn]
        )
        successful = details_resp.get("successfulSet", [])
        description = ""
        if successful:
            event_description = successful[0].get("eventDescription", {})
            description = event_description.get("latestDescription", "")
    except ClientError as e:
        logger.warning(f"DescribeEventDetails failed for {event_arn}: {e}")
        description = ""

    # Affected entities (resources)
    entities_filter = {"eventArns": [event_arn]}
    entities_method = (
        health.describe_affected_entities_for_organization
        if org_mode
        else health.describe_affected_entities
    )
    affected_entities = []
    try:
        if org_mode:
            # Org-mode requires per-account fan-out; first get affected accounts
            accts_resp = health.describe_affected_accounts_for_organization(
                eventArn=event_arn
            )
            affected_accounts = accts_resp.get("affectedAccounts", [])
            for acct in affected_accounts:
                per_acct = entities_method(
                    organizationEntityFilters=[
                        {"eventArn": event_arn, "awsAccountId": acct}
                    ]
                )
                for ent in per_acct.get("entities", []):
                    ent["awsAccountId"] = acct  # ensure field present
                    affected_entities.append(ent)
        else:
            ents_resp = entities_method(filter={"eventArns": [event_arn]})
            affected_entities = ents_resp.get("entities", [])
    except ClientError as e:
        logger.warning(f"DescribeAffectedEntities failed for {event_arn}: {e}")

    # Build the EventBridge-shaped `detail` dict. Field names mirror
    # https://docs.aws.amazon.com/health/latest/ug/aws-health-events-eventbridge-schema.html
    detail = {
        "eventArn": event_arn,
        "service": event.get("service", "UNKNOWN"),
        "eventTypeCode": event.get("eventTypeCode", ""),
        "eventTypeCategory": event.get("eventTypeCategory", ""),
        "eventScopeCode": event.get("eventScopeCode", "NONE"),
        "startTime": str(event.get("startTime", "")),
        "lastUpdatedTime": str(event.get("lastUpdatedTime", "")),
        "statusCode": event.get("statusCode", "open"),
        "eventRegion": event.get("region", "global"),
        "eventDescription": {"latestDescription": description},
        "affectedEntities": [
            {
                "entityValue": ent.get("entityValue", ""),
                "awsAccountId": ent.get("awsAccountId", ""),
            }
            for ent in affected_entities
        ],
    }
    return detail


def _envelope_for_event(event, account_id: str):
    """Build the outer EventBridge envelope the collector expects."""
    return {
        "account": account_id,
        "region": event.get("region", "global"),
    }


def backfill(days: int, org_mode: bool, role_arn: str | None, dry_run: bool) -> int:
    """Main entry. Returns event count (or -1 on subscription error)."""
    _load_collector_config()
    _configure_role_arn(role_arn)
    handler = _import_handler()

    health = _aws_health_client()
    account_id = _sts_caller_account()

    start_time_utc = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        if org_mode:
            events = _describe_events_org(health, start_time_utc)
        else:
            events = _describe_events_single_account(health, start_time_utc)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "SubscriptionRequiredException":
            logger.error(
                "\n  AWS Health API requires Business Support or higher.\n"
                "  This account appears to be on Basic or Developer Support.\n"
                "  EventBridge-based ingestion still works without a paid plan;\n"
                "  only this backfill command requires the API gate.\n"
                "  See https://aws.amazon.com/premiumsupport/plans/ for upgrade options."
            )
            return -1
        raise

    if not events:
        logger.info("No events returned by AWS Health for the requested window.")
        return 0

    if dry_run:
        logger.info(f"[dry-run] Would backfill {len(events)} events into DynamoDB")
        for e in events[:5]:
            logger.info(
                f"  {e.get('arn')} {e.get('service')} {e.get('eventTypeCategory')} "
                f"{e.get('statusCode')} {e.get('lastUpdatedTime')}"
            )
        if len(events) > 5:
            logger.info(f"  ... and {len(events)-5} more")
        return len(events)

    # Real backfill — stream each event through the collector's processor.
    # DDB lives in the local (collector) account — no cross-account assume.
    # Writing into the mgmt account's DDB from an ops account would be weird;
    # the collector's whole point is "deploy-account local storage".
    table = boto3.resource("dynamodb").Table(os.environ["HEALTH_EVENTS_TABLE_NAME"])
    ok = failed = 0
    t0 = time.monotonic()
    for i, ev in enumerate(events, 1):
        try:
            detail = _enrich_event_details(health, ev, org_mode)
            envelope = _envelope_for_event(ev, account_id)
            handler._process_health_event(detail, envelope, table)
            ok += 1
        except Exception as e:
            logger.error(
                f"[{i}/{len(events)}] {ev.get('arn', '?')} failed: {type(e).__name__}: {e}"
            )
            failed += 1
        if i % 10 == 0:
            elapsed = time.monotonic() - t0
            logger.info(
                f"Progress: {i}/{len(events)} "
                f"(ok={ok} fail={failed} elapsed={elapsed:.1f}s)"
            )

    elapsed = time.monotonic() - t0
    logger.info(
        f"Backfill complete: {ok} stored, {failed} failed, {elapsed:.1f}s total"
    )
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Backfill window in days (max 90 — AWS Health retention limit). Default: 30.",
    )
    parser.add_argument(
        "--org",
        action="store_true",
        help="Use org-view APIs (DescribeEventsForOrganization) to pull events for all member accounts. "
        "Requires health:* org-view enabled + Business+ Support. Run from mgmt account or delegated admin.",
    )
    parser.add_argument(
        "--role-arn",
        default=None,
        help="Optional IAM role ARN to assume before calling Health/Organizations. "
        "Use when the caller is an ops account and needs to reach the mgmt/delegated-admin context.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List events that WOULD be backfilled, but don't write to DynamoDB.",
    )
    args = parser.parse_args()

    if args.days > 90:
        logger.warning(
            "AWS Health retains events for 90 days. --days=%d is clamped to 90.",
            args.days,
        )
        args.days = 90

    try:
        count = backfill(
            days=args.days,
            org_mode=args.org,
            role_arn=args.role_arn,
            dry_run=args.dry_run,
        )
        sys.exit(0 if count >= 0 else 2)
    except KeyboardInterrupt:
        logger.error("Interrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()
