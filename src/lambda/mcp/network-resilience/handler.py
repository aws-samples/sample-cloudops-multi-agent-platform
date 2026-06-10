"""AgentCore Gateway-facing handler for the network-resilience MCP tool.

Routing contract (shared with every MCP Lambda on the gateway):
    context.client_context.custom["bedrockAgentCoreToolName"] = "<target>___<tool_name>"
    event body = tool params (NOT wrapped)

Phase 1 exposed: ``get_today_date``, ``discover_dx_topology``.

Phase 2 adds four more:
    - ``assess_dx_resiliency``: run the 5 resiliency rules + 17 best-practice
      checks + 2 SLA attestations, return a ``CombinedAssessment`` with
      per-DXGW scores, global recommendations, and ghost-node specs.
    - ``get_recommendation_details``: expand a single recommendation by ID
      (useful when an agent wants to drill into one without re-sending the
      whole topology).
    - ``get_dx_pricing``: live DX port + data-transfer pricing via the
      AWS Pricing API.
    - ``estimate_upgrade_cost``: delta cost to move a DXGW from its current
      tier to a target tier. Combines topology shape + pricing lookup.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import Any, Dict, Optional

from network_resilience.engine.pricing import (
    lookup_dx_pricing,
    lookup_network_service_pricing,
)
from network_resilience.engine.recommendation_engine import analyze_topology
from network_resilience.engine.sla_gating import get_location_device_counts
from network_resilience.topology.fetch import fetch_all_topology_data
from network_resilience.topology.mocks import available_scenarios

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    tool_name = _resolve_tool_name(context)
    logger.info("network-resilience invoke: tool=%s", tool_name)

    handlers = {
        "get_today_date": _handle_get_today_date,
        "discover_dx_topology": _handle_discover_dx_topology,
        "assess_dx_resiliency": _handle_assess_dx_resiliency,
        "get_recommendation_details": _handle_get_recommendation_details,
        "get_dx_pricing": _handle_get_dx_pricing,
        "estimate_upgrade_cost": _handle_estimate_upgrade_cost,
    }
    fn = handlers.get(tool_name)
    if fn is None:
        return {
            "error": f"Unknown tool: {tool_name}",
            "available_tools": list(handlers.keys()),
        }
    try:
        return fn(event or {})
    except Exception as err:  # noqa: BLE001 — surface the error to the agent
        logger.exception("tool handler raised")
        return {"error": f"{tool_name} failed: {err}"}


# ----- Tool implementations -------------------------------------------------


def _handle_get_today_date(_event: Dict[str, Any]) -> Dict[str, Any]:
    """UTC today in ``YYYY-MM-DD``. Lets the agent ground "last month"-style
    queries without guessing.
    """
    today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    return {"status": "success", "data": {"today": today}}


def _handle_discover_dx_topology(event: Dict[str, Any]) -> Dict[str, Any]:
    """Discover the caller's AWS Direct Connect topology.

    Optional params:
        default_region (str): home region to seed discovery from. Falls back
            to the Lambda's AWS_REGION env var, then us-east-1.
        mock_scenario (str): bypass AWS and return one of the six fixture
            scenarios. Valid: noResiliency, devTest, high, maximum,
            crossAccount, cloudWan.

    The returned topology is a TopologyData dict (see
    network_resilience/types.py) plus a ``fetchErrors`` list describing any
    partial failures. Agents should surface fetchErrors to the user rather
    than retry blindly.
    """
    default_region = event.get("default_region") or os.environ.get(
        "DEFAULT_REGION"
    )
    mock_scenario = event.get("mock_scenario") or os.environ.get("MOCK_SCENARIO")

    if mock_scenario and mock_scenario not in available_scenarios():
        return {
            "status": "error",
            "error": f"unknown mock_scenario: {mock_scenario}",
            "valid_scenarios": available_scenarios(),
        }

    topology = fetch_all_topology_data(
        default_region=default_region, mock_scenario=mock_scenario
    )
    return {"status": "success", "data": topology}


def _handle_assess_dx_resiliency(event: Dict[str, Any]) -> Dict[str, Any]:
    """Run the full assessment engine against a topology.

    Two invocation modes:
        1. Caller supplies ``topology`` dict → skip discovery, assess directly.
        2. Caller supplies no topology → fetch via default_region/mock_scenario
           first (equivalent to calling discover_dx_topology + assess).

    Optional ``targets`` can be a scalar ("high"/"maximum", applied to all
    DXGWs) or a dict keyed by DXGW ID. Missing targets default to "high".
    """
    topology = event.get("topology")
    if topology is None:
        default_region = event.get("default_region") or os.environ.get(
            "DEFAULT_REGION"
        )
        mock_scenario = event.get("mock_scenario") or os.environ.get(
            "MOCK_SCENARIO"
        )
        if mock_scenario and mock_scenario not in available_scenarios():
            return {
                "status": "error",
                "error": f"unknown mock_scenario: {mock_scenario}",
                "valid_scenarios": available_scenarios(),
            }
        topology = fetch_all_topology_data(
            default_region=default_region, mock_scenario=mock_scenario
        )

    targets = event.get("targets", "high")
    assessment = analyze_topology(topology, targets)
    # Pair topology with assessment so the visualizer can render the graph
    # AND the recommendations from a single tool call. Without `topology` here
    # the frontend has no nodes/edges to draw — assessment alone is useless.
    return {
        "status": "success",
        "data": {"topology": topology, "assessment": assessment},
    }


def _handle_get_recommendation_details(
    event: Dict[str, Any],
) -> Dict[str, Any]:
    """Re-run assessment and extract a single recommendation by ID.

    Simpler than threading recommendations through the agent's turn-taking
    memory — the agent can call this tool with an ID and get back the full
    ``Recommendation`` dict (title, description, severity, ghost-node specs).
    """
    rec_id = event.get("recommendation_id")
    if not rec_id:
        return {
            "status": "error",
            "error": "recommendation_id is required",
        }

    topology = event.get("topology")
    if topology is None:
        default_region = event.get("default_region") or os.environ.get(
            "DEFAULT_REGION"
        )
        mock_scenario = event.get("mock_scenario") or os.environ.get(
            "MOCK_SCENARIO"
        )
        topology = fetch_all_topology_data(
            default_region=default_region, mock_scenario=mock_scenario
        )

    targets = event.get("targets", "high")
    assessment = analyze_topology(topology, targets)

    # Search every recommendation bucket; perDxGateway + global + aggregate.
    buckets = []
    for dxgw in assessment.get("perDxGateway") or []:
        buckets.extend(dxgw.get("recommendations") or [])
    g = assessment.get("global") or {}
    buckets.extend((g.get("resiliency") or {}).get("recommendations") or [])
    buckets.extend(
        (g.get("bestPractice") or {}).get("recommendations") or []
    )
    buckets.extend(
        (assessment.get("resiliency") or {}).get("recommendations") or []
    )
    buckets.extend(
        (assessment.get("bestPractice") or {}).get("recommendations") or []
    )

    for rec in buckets:
        if rec.get("id") == rec_id:
            return {"status": "success", "data": rec}
    return {
        "status": "error",
        "error": f"recommendation not found: {rec_id}",
    }


def _handle_get_dx_pricing(event: Dict[str, Any]) -> Dict[str, Any]:
    """Live DX port + data-transfer pricing for a region + port speed."""
    region = event.get("region")
    port_speed = event.get("port_speed")
    num_connections = int(event.get("num_connections", 1))

    if not region or not port_speed:
        return {
            "status": "error",
            "error": "region and port_speed are required",
        }
    valid_speeds = {"1Gbps", "10Gbps", "100Gbps"}
    if port_speed not in valid_speeds:
        return {
            "status": "error",
            "error": f"port_speed must be one of {sorted(valid_speeds)}",
        }

    result = lookup_dx_pricing(region, port_speed, num_connections)
    return {"status": "success", "data": result}


def _handle_estimate_upgrade_cost(event: Dict[str, Any]) -> Dict[str, Any]:
    """Estimate the delta monthly cost to reach a target tier from the
    topology's current shape.

    This combines topology shape (location/device counts) with live DX
    pricing. It's a rough estimate — actual costs include data transfer,
    cross-connect fees (partner-dependent), and operational overhead not
    modeled here.
    """
    topology = event.get("topology")
    if topology is None:
        default_region = event.get("default_region") or os.environ.get(
            "DEFAULT_REGION"
        )
        mock_scenario = event.get("mock_scenario") or os.environ.get(
            "MOCK_SCENARIO"
        )
        topology = fetch_all_topology_data(
            default_region=default_region, mock_scenario=mock_scenario
        )

    target_tier = event.get("target_tier", "high")
    if target_tier not in ("high", "maximum"):
        return {
            "status": "error",
            "error": "target_tier must be 'high' or 'maximum'",
        }

    port_speed = event.get("port_speed", "10Gbps")
    region: Optional[str] = event.get("region")
    if not region:
        conns = topology.get("connections") or []
        if conns:
            region = conns[0].get("region")
    if not region:
        return {
            "status": "error",
            "error": (
                "region required; could not infer from topology "
                "(no connections)"
            ),
        }

    # Count the gap: how many additional connections are needed.
    location_devices = get_location_device_counts(topology)
    current_locations = len(location_devices)
    current_devices_total = sum(location_devices.values())

    if target_tier == "high":
        # Need ≥2 locations with ≥1 connection each.
        target_connections = max(2, current_locations)
        additional_connections = max(0, 2 - current_locations)
    else:  # maximum
        # Need ≥2 locations with ≥2 distinct devices each.
        target_locations = max(2, current_locations)
        target_connections = target_locations * 2
        needed_devices = target_locations * 2
        additional_connections = max(0, needed_devices - current_devices_total)

    pricing = lookup_dx_pricing(region, port_speed, additional_connections)
    additional_monthly = pricing.get("totalMonthlyPortCost", 0)

    current_monthly_estimate = pricing.get(
        "monthlyPortCostPerConnection", 0
    ) * max(1, len(topology.get("connections") or []))

    return {
        "status": "success",
        "data": {
            "currentTier": _current_tier_from_devices(location_devices),
            "targetTier": target_tier,
            "currentLocations": current_locations,
            "targetConnections": target_connections,
            "additionalConnectionsNeeded": additional_connections,
            "additionalMonthlyCost": additional_monthly,
            "currentMonthlyCostEstimate": round(current_monthly_estimate, 2),
            "targetMonthlyCostEstimate": round(
                current_monthly_estimate + additional_monthly, 2
            ),
            "currency": "USD",
            "pricingDetails": pricing,
            "notes": (
                "Estimate excludes data transfer, partner cross-connect fees, "
                "and any LAG bundling discounts. Treat as rough budgeting."
            ),
        },
    }


def _current_tier_from_devices(
    location_devices: Dict[str, int],
) -> str:
    if not location_devices:
        return "none"
    loc_count = len(location_devices)
    all_multi = all(c >= 2 for c in location_devices.values())
    if loc_count >= 2 and all_multi:
        return "maximum"
    if loc_count >= 2:
        return "high"
    return "devtest"


# ----- Routing --------------------------------------------------------------


def _resolve_tool_name(context: Any) -> str:
    """Extract the tool name from the AgentCore Gateway invocation envelope.

    Format: ``<gateway_target>___<tool_name>``. In local invokes (unit tests,
    manual SDK probes without a gateway), callers may pass
    ``bedrockAgentCoreToolName`` directly on the event or use the raw tool
    name — both fall back cleanly to "unknown".
    """
    try:
        extended = context.client_context.custom["bedrockAgentCoreToolName"]
    except (AttributeError, KeyError, TypeError):
        return "unknown"
    if "___" in extended:
        return extended.split("___", 1)[1]
    return extended
