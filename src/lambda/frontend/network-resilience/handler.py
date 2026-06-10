"""Network-resiliency REST API Lambda — separate from the core frontend-api.

This Lambda handles browser-side operations that need fast server-side
compute but do NOT need LLM reasoning:

    POST /network-resilience/reassess
        Body: {topology, targetTiers: {dxgwId: "high"|"maximum"}}
        Returns: {assessment}
        Use: visualizer tier-picker toggle. <500ms target. The rules engine
        is the same one used by the MCP Lambda's ``assess_dx_resiliency``
        tool (shared ``network_resilience/engine/`` package), so results
        never drift between the two paths.

    POST /network-resilience/live-status
        Body: {vifIds: [...], region?: str}
        Returns: {vifId: {accepted, advertised}}
        Use: visualizer live-status overlay, polled every 60s while enabled.

    POST /network-resilience/utilization
        Body: {vifIds?: [...], connectionIds?: [...], region?: str,
               windowDays: 30 | 60 | 90}
        Returns: {region, windowDays,
                  vif: {vifId: {ingressBpsPeak?, egressBpsPeak?}},
                  connection: {connId: {ingressBpsPeak?, egressBpsPeak?}}}
        Use: visualizer "Show utilization" overlay. On-demand only — fetched
        when the user toggles utilization on or changes the window selector.
        Each call bills CloudWatch GetMetricData per matching stream, so the
        client caches results per-window for the session.

    POST /network-resilience/cross-account-enrich
        Body: {topology, roleArns: [...]}
        Returns: {additionalVpcs, additionalTgws, additionalTgwAttachments}
        Use: optional Phase 7 spoke-account enrichment via AssumeRole.

    GET /network-resilience/health
        Returns: {status: "ok", version: str}
        Use: liveness probe + frontend feature-detect ("is this API
        deployed? show the fast-path buttons if yes").

All routes require a Cognito JWT (audience = frontend app client). Auth is
enforced by the API Gateway authorizer, not this handler — by the time a
request lands here, the JWT has already been validated. actor_id is
extracted from the JWT ``email`` claim using the same sanitization as the
core frontend-api Lambda (@ → _at_, . → _) so downstream audit trails line
up across both Lambdas.

The Lambda zip ships a copy of ``network_resilience/`` alongside this
handler (the Makefile copies it in from
``src/lambda/mcp/network-resilience/network_resilience/``). Only the
engine + types modules are actually imported; topology fetchers are present
but unused here.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config

from network_resilience.engine.recommendation_engine import analyze_topology

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_VERSION = "0.1.0"

# Same sanitization rule as src/lambda/frontend/handler.py so actor IDs match.
_SANITIZE_AT = "_at_"


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """API Gateway v2 HTTP API proxy event entrypoint."""
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath") or event.get("requestContext", {}).get(
        "http", {}
    ).get("path", "")
    logger.info("network-resilience-api: %s %s", method, path)

    route = f"{method} {path}"
    try:
        if route == "GET /network-resilience/health":
            return _ok({"status": "ok", "version": _VERSION})
        if route == "POST /network-resilience/reassess":
            return _route_reassess(_parse_body(event))
        if route == "POST /network-resilience/live-status":
            return _route_live_status(_parse_body(event))
        if route == "POST /network-resilience/utilization":
            return _route_utilization(_parse_body(event))
        if route == "POST /network-resilience/cross-account-enrich":
            return _route_cross_account_enrich(_parse_body(event))
        return _error(404, f"Route not found: {route}")
    except ValueError as err:  # malformed request body or bad params
        return _error(400, str(err))
    except Exception as err:  # noqa: BLE001
        logger.exception("handler error on %s", route)
        return _error(500, f"Internal error: {err}")


# ----- Route implementations ------------------------------------------------


def _route_reassess(body: Dict[str, Any]) -> Dict[str, Any]:
    """Re-run the 5 resiliency + 17 best-practice rules with custom target
    tiers on a topology the browser already has. Pure compute; no AWS calls.
    """
    topology = body.get("topology")
    if not isinstance(topology, dict):
        raise ValueError("topology (object) is required")

    target_tiers = body.get("targetTiers")
    if target_tiers is None:
        targets = "high"
    elif isinstance(target_tiers, str):
        if target_tiers not in ("high", "maximum"):
            raise ValueError("targetTiers scalar must be 'high' or 'maximum'")
        targets = target_tiers
    elif isinstance(target_tiers, dict):
        for v in target_tiers.values():
            if v not in ("high", "maximum"):
                raise ValueError(
                    "targetTiers values must each be 'high' or 'maximum'"
                )
        targets = target_tiers
    else:
        raise ValueError(
            "targetTiers must be a string, object, or omitted (defaults to 'high')"
        )

    assessment = analyze_topology(topology, targets)
    return _ok({"assessment": assessment})


def _route_live_status(body: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch current BGP prefix counters for a set of VIFs via CloudWatch.

    Body:
        vifIds: list[str] — VIF IDs to poll
        region: str (optional) — CloudWatch region. Defaults to the Lambda's
            AWS_REGION. Real topologies may span multiple regions; callers
            should group by region and call this once per region.
    """
    vif_ids = body.get("vifIds")
    if not isinstance(vif_ids, list) or not vif_ids:
        raise ValueError("vifIds (non-empty array) is required")
    if not all(isinstance(v, str) for v in vif_ids):
        raise ValueError("vifIds must be an array of strings")

    region = body.get("region") or os.environ.get("AWS_REGION") or "us-east-1"
    result = _fetch_bgp_counts(region, vif_ids)
    return _ok({"region": region, "metrics": result})


def _route_utilization(body: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch peak hourly bps utilization per VIF + per DX Connection over a
    30/60/90 day window. Same shared package as the MCP topology fetcher,
    so the math matches what the agent's discover_dx_topology would produce.

    The browser already has the VIF + connection inventory from the cached
    topology and just wants peak counters — no full discovery needed.
    """
    vif_ids = body.get("vifIds") or []
    conn_ids = body.get("connectionIds") or []
    if not isinstance(vif_ids, list) or not isinstance(conn_ids, list):
        raise ValueError("vifIds and connectionIds must be arrays")
    if not vif_ids and not conn_ids:
        raise ValueError(
            "at least one of vifIds or connectionIds must be non-empty"
        )
    if not all(isinstance(v, str) for v in vif_ids):
        raise ValueError("vifIds must be an array of strings")
    if not all(isinstance(c, str) for c in conn_ids):
        raise ValueError("connectionIds must be an array of strings")

    window_days = body.get("windowDays")
    if window_days not in (30, 60, 90):
        raise ValueError("windowDays must be 30, 60, or 90")

    region = body.get("region") or os.environ.get("AWS_REGION") or "us-east-1"

    # Reuse the shared fetcher in network_resilience.topology.cloudwatch_dx —
    # same code path the discover_dx_topology MCP tool would call. We synth
    # minimal VIF / Connection dicts because the fetcher only reads
    # virtualInterfaceId, connectionId, and region from each.
    from network_resilience.topology.cloudwatch_dx import fetch_utilization

    vifs = [{"virtualInterfaceId": vid, "region": region} for vid in vif_ids]
    conns = [{"connectionId": cid, "region": region} for cid in conn_ids]
    result = fetch_utilization(vifs, conns, region, window_days)
    return _ok(
        {
            "region": region,
            "windowDays": window_days,
            "vif": result.get("vif", {}),
            "connection": result.get("connection", {}),
        }
    )


def _route_cross_account_enrich(body: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 7 stub. AssumeRole path not enabled in Phase 4 — but the route
    exists so the frontend can feature-detect without a 404.
    """
    role_arns = body.get("roleArns")
    if not isinstance(role_arns, list):
        raise ValueError("roleArns (array) is required")
    return _ok(
        {
            "additionalVpcs": [],
            "additionalTgws": [],
            "additionalTgwAttachments": [],
            "note": (
                "cross-account enrichment is scaffolded but disabled in "
                "Phase 4. Phase 7 wires up AssumeRole-based spoke VPC "
                "discovery."
            ),
        }
    )


# ----- CloudWatch BGP metrics helper ----------------------------------------


_CLIENT_CONFIG = Config(
    connect_timeout=5,
    read_timeout=15,
    retries={"max_attempts": 2, "mode": "standard"},
)


def _fetch_bgp_counts(
    region: str, vif_ids: List[str]
) -> Dict[str, Dict[str, int]]:
    """Tiny wrapper around CloudWatch GetMetricData — returns a map of
    ``{vifId: {accepted?, advertised?}}``. Missing VIFs silently omit.

    Different from ``network_resilience.topology.cloudwatch_dx`` which does
    a full multi-region + ListMetrics discovery. Here the browser already
    has the VIF inventory from the cached topology and just wants fresh
    counter values — no discovery needed.
    """
    if not vif_ids:
        return {}
    cw = boto3.client("cloudwatch", region_name=region, config=_CLIENT_CONFIG)
    import datetime as _dt

    now = _dt.datetime.now(tz=_dt.timezone.utc)
    start = now - _dt.timedelta(minutes=30)

    queries = []
    lookup: Dict[str, Dict[str, Any]] = {}
    qid = 0
    for vif_id in vif_ids:
        for metric_name in (
            "VirtualInterfaceBgpPrefixesAccepted",
            "VirtualInterfaceBgpPrefixesAdvertised",
        ):
            label = f"m{qid}"
            qid += 1
            queries.append(
                {
                    "Id": label,
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/DX",
                            "MetricName": metric_name,
                            "Dimensions": [
                                {"Name": "VirtualInterfaceId", "Value": vif_id}
                            ],
                        },
                        "Period": 300,
                        "Stat": "Average",
                    },
                    "ReturnData": True,
                }
            )
            lookup[label] = {
                "vifId": vif_id,
                "isAccepted": metric_name
                == "VirtualInterfaceBgpPrefixesAccepted",
            }

    out: Dict[str, Dict[str, int]] = {}
    # GetMetricData accepts up to 500 queries per call
    for i in range(0, len(queries), 500):
        batch = queries[i : i + 500]
        resp = cw.get_metric_data(
            MetricDataQueries=batch, StartTime=start, EndTime=now
        )
        for r in resp.get("MetricDataResults") or []:
            lbl = r.get("Id")
            vals = r.get("Values") or []
            if not lbl or not vals:
                continue
            info = lookup.get(lbl)
            if not info:
                continue
            value = int(round(vals[0]))
            entry = out.setdefault(info["vifId"], {})
            entry["accepted" if info["isAccepted"] else "advertised"] = value
    return out


# ----- Response helpers + auth extraction -----------------------------------


def _ok(body: Any) -> Dict[str, Any]:
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _error(status: int, message: str) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"error": message}),
    }


def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as err:
        raise ValueError(f"invalid JSON body: {err}") from err
    if not isinstance(parsed, dict):
        raise ValueError("request body must be a JSON object")
    return parsed


def _actor_id_from_event(event: Dict[str, Any]) -> Optional[str]:
    """Extract actor_id from the Cognito JWT email claim, sanitized to match
    the core frontend-api Lambda's convention (@ → _at_, . → _). Returns
    None when the claim is missing (auth shouldn't let it happen, but the
    handler stays defensive).
    """
    try:
        jwt_claims = (
            event["requestContext"]["authorizer"]["jwt"]["claims"] or {}
        )
    except (KeyError, TypeError):
        return None
    email = jwt_claims.get("email")
    if not email:
        return None
    return re.sub(r"[.]", "_", email.replace("@", _SANITIZE_AT))
