"""
Health Events Collector — processes AWS Health events from EventBridge via SQS.

Triggered by SQS queue that receives EventBridge health events.
Enriches events with account names, rules-based risk analysis, and (optionally)
LLM-generated narrative fields, then stores in DynamoDB.

Architecture:
    EventBridge (aws.health) → SQS → This Lambda → DynamoDB (health events table)

Enrichment split:
    Rules-based (deterministic, always runs):
        riskLevel        — CRITICAL/HIGH/MEDIUM/LOW (see _assess_risk)
        accountName      — resolved via Organizations API, cached
    LLM-based (Haiku 4.5, best-effort, failures don't block ingest):
        impactSummary         — one-sentence operational summary
        remediationHint       — what an operator should check/do
        affectedResourceTypes — normalized resource-type tokens

Why the split: priority labels are operationally load-bearing and must be
explainable, so they stay deterministic. Narrative fields are nice-to-have
summaries that save LLM tokens for the downstream agent when answering
queries — wrong/missing narrative degrades UX but can't produce a wrong
severity classification.
"""

import json
import logging
import os
import traceback
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from botocore.config import Config as BotoConfig

# shared/ is copied into the zip by `make package` (Makefile collector loop).
# Use the unified cross-account helper so adding new aliases, external IDs, or
# session-cache logic happens in ONE place (src/lambda/mcp/shared/cross_account.py).
# Alias "HEALTH" is reserved for this collector; when CROSS_ACCOUNT_ROLE_ARN_HEALTH
# is unset, get_aws_client transparently falls back to the execution role.
from shared.cross_account import get_aws_client  # noqa: E402

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

TABLE_NAME = os.environ.get("HEALTH_EVENTS_TABLE_NAME", "")
TTL_DAYS = int(os.environ.get("EVENTS_TTL_DAYS", "180"))

# Claude Haiku 4.5 via Bedrock cross-region inference. Set to empty string in
# the Lambda env to disable enrichment entirely (collector still writes the
# rules-based fields — just no narrative).
ENRICHMENT_MODEL_ID = os.environ.get(
    "ENRICHMENT_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0"
)
# Bounded timeout so a flaky Bedrock endpoint can't starve SQS batch processing.
# Lambda's SQS event-source visibility timeout is 960s; per-event LLM is capped
# here to keep a ~1s p50 ingest path.
_ENRICHMENT_TIMEOUT_S = int(os.environ.get("ENRICHMENT_TIMEOUT_S", "5"))
_ENRICHMENT_MAX_TOKENS = int(os.environ.get("ENRICHMENT_MAX_TOKENS", "300"))

# Module-level Bedrock client, lazy-initialised on first call. Warm Lambdas
# reuse it across invocations within the same container.
_bedrock_client = None


def handler(event, context):
    """Process SQS records containing EventBridge health events."""
    logger.info(f"Processing {len(event.get('Records', []))} SQS records")

    if not TABLE_NAME:
        logger.error("HEALTH_EVENTS_TABLE_NAME not set")
        return {
            "batchItemFailures": [
                {"itemIdentifier": r["messageId"]} for r in event.get("Records", [])
            ]
        }

    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    failures = []

    for record in event.get("Records", []):
        try:
            body = json.loads(record["body"])
            detail = body.get("detail", {})
            if not detail:
                logger.warning(f"No detail in record {record['messageId']}")
                continue

            _process_health_event(detail, body, table)
        except Exception as e:
            logger.error(f"Failed to process record {record['messageId']}: {e}")
            logger.debug(traceback.format_exc())
            failures.append({"itemIdentifier": record["messageId"]})

    logger.info(
        f"Processed {len(event.get('Records', [])) - len(failures)} events, {len(failures)} failures"
    )
    return {"batchItemFailures": failures}


def _process_health_event(detail: dict, envelope: dict, table) -> None:
    """Process a single health event and store in DynamoDB."""
    event_arn = detail.get("eventArn", "")
    if not event_arn:
        return

    # Extract affected accounts from the event
    affected = detail.get("affectedEntities", [])
    account_ids = list(
        {e.get("awsAccountId", "") for e in affected if e.get("awsAccountId")}
    )
    if not account_ids:
        # Fall back to the account from the envelope
        account_ids = [envelope.get("account", "")]

    # Extract event fields
    service = detail.get("service", "UNKNOWN")
    event_type_code = detail.get("eventTypeCode", "")
    event_type_category = detail.get("eventTypeCategory", "")
    event_scope_code = detail.get("eventScopeCode", "NONE")
    region = detail.get("eventRegion", envelope.get("region", "global"))
    status_code = detail.get("statusCode", "open")
    start_time = detail.get("startTime", "")
    last_update = detail.get(
        "lastUpdatedTime", start_time or datetime.now(timezone.utc).isoformat()
    )

    # Extract description
    description = ""
    event_desc = detail.get("eventDescription", {})
    if isinstance(event_desc, dict):
        description = event_desc.get("latestDescription", "")
    elif isinstance(event_desc, list) and event_desc:
        description = (
            event_desc[0].get("latestDescription", "")
            if isinstance(event_desc[0], dict)
            else str(event_desc[0])
        )

    # Affected resources
    resources = []
    for entity in affected:
        val = entity.get("entityValue", "")
        if val:
            resources.append(val)

    # Risk analysis — deterministic, uses signals AWS gives us for free.
    # See _assess_risk docstring for the full rule set.
    risk_level = _assess_risk(
        category=event_type_category,
        status=status_code,
        service=service,
        scope_code=event_scope_code,
        start_time_iso=start_time,
    )

    # Calculate TTL
    try:
        if last_update and last_update != "N/A":
            if last_update.endswith("Z"):
                last_update = last_update.replace("Z", "+00:00")
            dt = datetime.fromisoformat(last_update)
        else:
            dt = datetime.now(timezone.utc)
        ttl = int((dt + timedelta(days=TTL_DAYS)).timestamp())
        last_update_iso = dt.isoformat()
    except Exception:
        dt = datetime.now(timezone.utc)
        ttl = int((dt + timedelta(days=TTL_DAYS)).timestamp())
        last_update_iso = dt.isoformat()

    # LLM narrative enrichment. Computed ONCE per event (not per affected
    # account) because the inputs — service, category, description,
    # resources — are account-agnostic. Each affected-account row gets the
    # same narrative fields. `_enrich_with_llm` returns {} on any failure;
    # we still write the row without the narrative.
    enrichment = _enrich_with_llm(
        service=service,
        event_type_code=event_type_code,
        category=event_type_category,
        scope_code=event_scope_code,
        status=status_code,
        description=description,
        resources=resources,
    )

    # Store one row per affected account
    for account_id in account_ids:
        if not account_id:
            continue
        account_name = _get_account_name(account_id)

        item = {
            "eventArn": event_arn,
            "accountId": account_id,
            "accountName": account_name,
            "service": service,
            "eventTypeCode": event_type_code,
            "eventTypeCategory": event_type_category,
            "eventScopeCode": event_scope_code,
            "region": region,
            "statusCode": status_code,
            "startTime": start_time or "N/A",
            "lastUpdateTime": last_update_iso,
            "description": description[:2000] if description else "No description",
            "riskLevel": risk_level,
            "affectedResources": ", ".join(resources[:10]) or "None specified",
            "ttl": ttl,
            "collectedAt": datetime.now(timezone.utc).isoformat(),
            # Narrative enrichment (may be absent on LLM failure or when
            # ENRICHMENT_MODEL_ID is blank). DDB allows sparse attributes.
            **enrichment,
        }
        # Convert for DynamoDB (handle floats → Decimal)
        item = json.loads(json.dumps(item), parse_float=Decimal)
        table.put_item(Item=item)
        logger.info(
            f"Stored event {event_arn} for account {account_id} "
            f"(status={status_code}, risk={risk_level}, enriched={bool(enrichment)})"
        )


# Services whose disruption typically impacts customer-facing workloads.
# Kept tight on purpose — additions here escalate events to CRITICAL, so
# we want high precision. Extend when observation justifies it.
_CORE_SERVICES = ("EC2", "RDS", "LAMBDA", "ECS", "EKS", "DYNAMODB", "S3")

# Scheduled-change urgency thresholds (days from now until startTime).
_SCHEDULED_IMMINENT_DAYS = 3
_SCHEDULED_SOON_DAYS = 14


def _assess_risk(
    *,
    category: str,
    status: str,
    service: str,
    scope_code: str = "NONE",
    start_time_iso: str = "",
) -> str:
    """Deterministic priority based on the fields AWS Health publishes.

    Priority inputs, in order of weight:
      1. eventTypeCategory   — issue > investigation > scheduledChange > accountNotification
      2. eventScopeCode      — ACCOUNT_SPECIFIC escalates (YOUR resources)
      3. statusCode          — open > upcoming > closed (for issues)
      4. service             — core services (EC2/RDS/etc) escalate open issues
      5. startTime proximity — imminent scheduledChange escalates

    Returns CRITICAL / HIGH / MEDIUM / LOW. Pure function; no side effects.
    """
    # Issues — the highest-weight category
    if category == "issue":
        if status == "open":
            # ACCOUNT_SPECIFIC open issues are the biggest signal AWS gives
            # us: your resources, right now, unresolved.
            if scope_code == "ACCOUNT_SPECIFIC":
                return "CRITICAL"
            if service in _CORE_SERVICES:
                return "CRITICAL"
            return "HIGH"
        # Closed issues are archival; keep visible but low-pri.
        return "LOW" if status == "closed" else "MEDIUM"

    # Investigations — AWS is actively looking into something impacting customers
    if category == "investigation":
        return "HIGH" if status == "open" else "MEDIUM"

    # Scheduled changes — urgency scales with time-to-event
    if category == "scheduledChange":
        days_out = _days_until(start_time_iso)
        if days_out is not None:
            if days_out <= _SCHEDULED_IMMINENT_DAYS and scope_code == "ACCOUNT_SPECIFIC":
                return "HIGH"
            if days_out <= _SCHEDULED_IMMINENT_DAYS:
                return "MEDIUM"
            if days_out <= _SCHEDULED_SOON_DAYS:
                return "MEDIUM"
        # Distant or unparseable startTime
        return "LOW" if status == "closed" else "MEDIUM"

    # Account notifications — always informational
    return "LOW"


def _days_until(iso_or_rfc: str):
    """Parse an AWS Health startTime into days-from-now. Returns None on failure.

    AWS Health emits startTime in two formats: ISO-8601 (from DescribeEvents
    API) and RFC-2822 (from EventBridge detail). Both handled.
    """
    if not iso_or_rfc or iso_or_rfc == "N/A":
        return None
    try:
        # ISO 8601
        s = iso_or_rfc.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    except Exception:
        try:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(iso_or_rfc)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt - datetime.now(timezone.utc)
    return delta.total_seconds() / 86400.0


# Cache account names to avoid repeated API calls
_account_name_cache: dict[str, str] = {}


def _get_account_name(account_id: str) -> str:
    """Resolve account ID to name via Organizations API (cached).

    Uses shared.cross_account.get_aws_client with role_alias="HEALTH" so when
    CROSS_ACCOUNT_ROLE_ARN_HEALTH is configured (collector in an ops account,
    Organizations API lives in the mgmt account), we assume into it. When
    unset, falls back to the execution role — same semantics as before.
    """
    if account_id in _account_name_cache:
        return _account_name_cache[account_id]
    try:
        org = get_aws_client("organizations", role_alias="HEALTH")
        resp = org.describe_account(AccountId=account_id)
        name = resp["Account"].get("Name", account_id)
        _account_name_cache[account_id] = name
        return name
    except Exception:
        _account_name_cache[account_id] = account_id
        return account_id


# -----------------------------------------------------------------------------
# Haiku 4.5 narrative enrichment
# -----------------------------------------------------------------------------
# This system prompt is static — same for every event — so Bedrock prompt
# caching cuts repeat input-token cost after the first call. Keep it verbose
# enough to pin the output format; the model is optimising for structure here
# more than prose.
_ENRICHMENT_SYSTEM_PROMPT = """\
You enrich AWS Health event records with concise operational metadata. You \
DO NOT assign severity labels — those are handled deterministically elsewhere. \
Your job is to produce narrative fields that help an SRE skim the event fast.

Return ONLY a JSON object with these exact keys:
  impactSummary          (string, <=140 chars, one sentence, plain English)
  remediationHint        (string, <=200 chars, or empty string if nothing actionable)
  affectedResourceTypes  (array of strings, lowercase hyphen-delimited tokens like \
"ec2-instance" / "rds-cluster" / "s3-bucket"; empty array if none derivable)

Rules:
  - No markdown, no prose outside the JSON.
  - No speculation about customer impact beyond what the description states.
  - If the event is informational (accountNotification) with no action required, \
remediationHint MUST be an empty string.
  - Resource types must be inferred from the affected-resources list, not invented.
"""


def _get_bedrock_client():
    """Lazy-init Bedrock runtime client. Reused across warm-Lambda invocations."""
    global _bedrock_client
    if _bedrock_client is None:
        region = os.environ.get("AWS_REGION", "us-east-1")
        # Tight timeouts — we'd rather degrade gracefully than stall SQS.
        config = BotoConfig(
            read_timeout=_ENRICHMENT_TIMEOUT_S,
            connect_timeout=2,
            retries={"max_attempts": 1, "mode": "standard"},
        )
        _bedrock_client = boto3.client(
            "bedrock-runtime", region_name=region, config=config
        )
    return _bedrock_client


def _enrich_with_llm(
    *,
    service: str,
    event_type_code: str,
    category: str,
    scope_code: str,
    status: str,
    description: str,
    resources: list,
) -> dict:
    """Return narrative enrichment dict, or {} on any failure.

    Best-effort: every exception path returns {} and logs a warning. Never
    raises — this is called in the ingest hot path and must not block writes.
    """
    if not ENRICHMENT_MODEL_ID:
        return {}
    if not description or description == "No description":
        # Nothing useful to summarise; skip the LLM round-trip.
        return {}

    # Compact user payload — keep it small; prompt caching won't help the user
    # side since these change per event.
    user_payload = {
        "service": service,
        "eventTypeCode": event_type_code,
        "category": category,
        "eventScope": scope_code,
        "status": status,
        "description": description[:1500],
        "affectedResources": resources[:10],
    }

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": _ENRICHMENT_MAX_TOKENS,
        "temperature": 0,
        "system": [
            {
                "type": "text",
                "text": _ENRICHMENT_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(user_payload, separators=(",", ":")),
                    }
                ],
            }
        ],
    }

    try:
        client = _get_bedrock_client()
        resp = client.invoke_model(
            modelId=ENRICHMENT_MODEL_ID, body=json.dumps(body)
        )
        payload = json.loads(resp["body"].read())
    except Exception as e:
        logger.warning(f"Enrichment LLM call failed ({type(e).__name__}): {e}")
        return {}

    # Expected shape: {"content": [{"type": "text", "text": "{...json...}"}], ...}
    try:
        text = "".join(
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type") == "text"
        )
        # Defensive: some models wrap JSON in code fences despite the prompt.
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.lstrip().lower().startswith("json"):
                text = text.split("\n", 1)[1]
            text = text.rsplit("```", 1)[0].strip()
        parsed = json.loads(text)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning(f"Enrichment JSON parse failed: {e}. Raw: {text[:200]!r}")
        return {}

    # Validate shape and enforce length limits defensively.
    impact = str(parsed.get("impactSummary", ""))[:140]
    remediation = str(parsed.get("remediationHint", ""))[:200]
    resource_types = parsed.get("affectedResourceTypes", [])
    if not isinstance(resource_types, list):
        resource_types = []
    resource_types = [
        str(rt)[:60] for rt in resource_types if isinstance(rt, (str, int))
    ][:10]

    # Log token usage for cost observability. Bedrock returns usage counts
    # in the response payload.
    usage = payload.get("usage", {})
    logger.info(
        f"Enrichment OK: in={usage.get('input_tokens', '?')} "
        f"out={usage.get('output_tokens', '?')} "
        f"cache_read={usage.get('cache_read_input_tokens', 0)}"
    )

    # Only include keys that have meaningful values — avoids littering DDB
    # with empty strings.
    out = {}
    if impact:
        out["impactSummary"] = impact
    if remediation:
        out["remediationHint"] = remediation
    if resource_types:
        out["affectedResourceTypes"] = resource_types
    return out
