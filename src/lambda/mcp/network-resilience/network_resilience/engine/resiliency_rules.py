"""Resiliency rules — the five tier-gap rules that emit ghost-node recommendations.

Python port of source ``dx-visualizer/src/engine/resiliency-rules.ts`` (237 lines).

Rules (all severity=info, category=resiliency):
    1. single-dx-location — only one DX location used; suggest a second
    2. single-connection-per-location — (Maximum target only) a location lacks
       2+ distinct AWS logical devices
    3. no-tgw — VPN gateways exist but no TGW
    4. single-vgw — exactly one VGW and no TGW
    5. no-lag — 2+ connections at same location but not bundled into a LAG

Ghost-node IDs match the source byte-for-byte — the frontend's
topology-builder keys off these prefixes. Do not change without a
corresponding frontend update.
"""

from __future__ import annotations

from typing import List, Optional

from ..types import (
    GhostEdgeSpec,
    GhostNodeSpec,
    Recommendation,
    ResiliencyTarget,
    TopologyData,
)
from .sla_gating import get_location_device_counts


# ----- Helpers --------------------------------------------------------------


def _ghost_node(
    node_id: str, category: str, label: str, **extra
) -> GhostNodeSpec:
    spec: GhostNodeSpec = {
        "id": node_id,
        "category": category,
        "label": label,
        "isRecommended": True,
    }
    if extra:
        # Source stores arbitrary extras under ``data`` — we flatten ``details``
        # since that's the only extra key rules emit.
        if "details" in extra:
            spec["details"] = extra["details"]
    return spec


def _ghost_edge(
    source: str,
    target: str,
    label: Optional[str] = None,
    label_position: Optional[float] = None,
) -> GhostEdgeSpec:
    spec: GhostEdgeSpec = {
        "id": f"e-rec-{source}-{target}",
        "source": source,
        "target": target,
        "isRecommended": True,
    }
    if label is not None:
        spec["label"] = label
    if label_position is not None:
        spec["labelPosition"] = label_position
    return spec


# ----- Rule 1: single-dx-location -------------------------------------------


def rule_single_dx_location(
    topology: TopologyData,
    target: ResiliencyTarget = "high",
    dx_gateway_id: Optional[str] = None,
    dx_gateway_name: Optional[str] = None,
) -> Optional[Recommendation]:
    """Topology uses only 1 DX location — suggest a second for site redundancy.

    Emits ghost nodes for: customer site, on-prem gateway, DX location, partner
    device, AWS device, and edges connecting them through the DXGW. For Maximum
    target, also adds a redundant device pair at the second location.
    """
    used_locations = {
        c.get("location")
        for c in topology.get("connections") or []
        if c.get("location")
    }
    if not used_locations:
        used_locations = {
            v.get("location")
            for v in topology.get("virtualInterfaces") or []
            if v.get("location")
        }
    if len(used_locations) >= 2 or len(used_locations) == 0:
        return None

    dx_gateways = topology.get("dxGateways") or []
    resolved_dxgw_id = dx_gateway_id or (
        dx_gateways[0].get("directConnectGatewayId") if dx_gateways else None
    )
    dxgw_node_id = f"dxgw-{resolved_dxgw_id}" if resolved_dxgw_id else None
    prefix = f"rec-{resolved_dxgw_id}" if resolved_dxgw_id else "rec"
    loc_code = f"{prefix}-loc-B"

    # Scoped-site label disambiguates overlapping ghost zones for multi-DXGW
    # topologies so users can tell recommendations apart visually.
    site_label = (
        f"Customer Data Center to support {dx_gateway_name or dx_gateway_id}"
        if dx_gateway_id
        else "Customer Data Center"
    )
    nodes: List[GhostNodeSpec] = [
        _ghost_node(
            f"{prefix}-custsite-B",
            "customerSite",
            site_label,
            details={"locationCode": loc_code, **({"dxGatewayId": dx_gateway_id} if dx_gateway_id else {})},
        ),
        _ghost_node(f"{prefix}-onprem-B", "onPremise", "Customer Gateway"),
        _ghost_node(
            f"{prefix}-dxloc-B",
            "dxLocation",
            "Second Direct Connect Location",
            details={"code": loc_code},
        ),
        _ghost_node(
            f"{prefix}-partner-B",
            "dxPartnerDevice",
            "Customer / Partner Device",
            details={"locationCode": loc_code},
        ),
        _ghost_node(
            f"{prefix}-awsdev-B",
            "awsDevice",
            "AWS Device",
            details={"locationCode": loc_code},
        ),
    ]

    edges: List[GhostEdgeSpec] = [
        _ghost_edge(f"{prefix}-onprem-B", f"{prefix}-partner-B"),
        _ghost_edge(f"{prefix}-partner-B", f"{prefix}-awsdev-B"),
    ]
    if dxgw_node_id:
        edges.append(
            _ghost_edge(f"{prefix}-awsdev-B", dxgw_node_id, "VIF", 0.2)
        )

    # For Maximum target, provision a redundant device pair at the new site.
    if target == "maximum":
        nodes.extend(
            [
                _ghost_node(
                    f"{prefix}-partner-B-2",
                    "dxPartnerDevice",
                    "Customer / Partner Device",
                    details={"locationCode": loc_code},
                ),
                _ghost_node(
                    f"{prefix}-awsdev-B-2",
                    "awsDevice",
                    "AWS Device",
                    details={"locationCode": loc_code},
                ),
            ]
        )
        edges.extend(
            [
                _ghost_edge(f"{prefix}-onprem-B", f"{prefix}-partner-B-2"),
                _ghost_edge(f"{prefix}-partner-B-2", f"{prefix}-awsdev-B-2"),
            ]
        )
        if dxgw_node_id:
            edges.append(
                _ghost_edge(
                    f"{prefix}-awsdev-B-2", dxgw_node_id, "VIF", 0.2
                )
            )

    sla_label = (
        "Maximum Resiliency (99.99% SLA)"
        if target == "maximum"
        else "High Resiliency (99.9% SLA)"
    )
    if target == "maximum":
        description = (
            "Your topology uses only one Direct Connect location. Adding a "
            f"second location with two redundant connections provides {sla_label} "
            "by eliminating both site and device failure."
        )
    else:
        description = (
            "Your topology uses only one Direct Connect location. Adding a "
            f"second location provides {sla_label} by eliminating single-site "
            "failure."
        )

    return {
        "id": f"rec-single-dx-location{('-' + resolved_dxgw_id) if resolved_dxgw_id else ''}",
        "ruleId": "single-dx-location",
        "category": "resiliency",
        # Tier-gap recommendations are advisory; a dev env may intentionally
        # sit on a single connection. Real faults stay Critical (best-practice
        # rules emit those).
        "severity": "info",
        "title": "Add a Second Direct Connect Location",
        "description": description,
        "additionalNodes": nodes,
        "additionalEdges": edges,
    }


# ----- Rule 2: single-connection-per-location (Maximum target only) --------


def rule_single_connection_per_location(
    topology: TopologyData,
    target: ResiliencyTarget = "high",
    dx_gateway_id: Optional[str] = None,
) -> List[Recommendation]:
    """Max tier requires 2+ distinct AWS logical devices per location. Emits
    one recommendation per location that has <2 devices. High tier doesn't
    need this — it only cares about site-level redundancy.
    """
    if target == "high":
        return []

    recs: List[Recommendation] = []
    location_devices = get_location_device_counts(topology)

    dx_gateways = topology.get("dxGateways") or []
    resolved_dxgw_id = dx_gateway_id or (
        dx_gateways[0].get("directConnectGatewayId") if dx_gateways else None
    )
    prefix = f"rec-{resolved_dxgw_id}" if resolved_dxgw_id else "rec"
    dxgw_node_id = f"dxgw-{resolved_dxgw_id}" if resolved_dxgw_id else None

    locations = topology.get("locations") or []
    connections = topology.get("connections") or []
    vifs = topology.get("virtualInterfaces") or []

    for location, device_count in location_devices.items():
        if device_count >= 2:
            continue

        loc_node = next(
            (l for l in locations if l.get("locationCode") == location), None
        )
        loc_name = (
            loc_node.get("locationName", location) if loc_node else location
        )

        nodes: List[GhostNodeSpec] = [
            _ghost_node(
                f"{prefix}-partner-{location}-2",
                "dxPartnerDevice",
                "Customer / Partner Device",
                details={"locationCode": location},
            ),
            _ghost_node(
                f"{prefix}-awsdev-{location}-2",
                "awsDevice",
                "AWS Device",
                details={"locationCode": location},
            ),
        ]

        on_prem_id = f"onprem-{location}"
        edges: List[GhostEdgeSpec] = [
            _ghost_edge(on_prem_id, f"{prefix}-partner-{location}-2"),
            _ghost_edge(
                f"{prefix}-partner-{location}-2",
                f"{prefix}-awsdev-{location}-2",
            ),
        ]
        if dxgw_node_id:
            edges.append(
                _ghost_edge(
                    f"{prefix}-awsdev-{location}-2",
                    dxgw_node_id,
                    "VIF",
                    0.2,
                )
            )

        # Distinguish "only 1 raw connection" from "N connections sharing 1
        # device" — both fail device redundancy, but the fix framing differs.
        if connections:
            raw_conn_count = sum(
                1 for c in connections if c.get("location") == location
            )
        else:
            raw_conn_count = sum(
                1 for v in vifs if (v.get("location") or "") == location
            )

        if raw_conn_count >= 2:
            description = (
                f"Location {loc_name} has {raw_conn_count} connections, but "
                "they terminate on the same AWS logical device — a device "
                "failure cuts this location entirely. Add a connection on a "
                "separate device to reach Maximum Resiliency (99.99% SLA)."
            )
        else:
            description = (
                f"Location {loc_name} has only one Direct Connect connection. "
                "Adding a second connection on a separate device provides "
                "Maximum Resiliency (99.99% SLA)."
            )

        recs.append(
            {
                "id": f"rec-single-conn-{location}{('-' + resolved_dxgw_id) if resolved_dxgw_id else ''}",
                "ruleId": "single-connection-per-location",
                "category": "resiliency",
                "severity": "info",
                "title": f"Add Redundant Connection at {loc_name}",
                "description": description,
                "additionalNodes": nodes,
                "additionalEdges": edges,
            }
        )

    return recs


# ----- Rule 3: no-tgw -------------------------------------------------------


def rule_no_tgw(topology: TopologyData) -> Optional[Recommendation]:
    """VPN gateways exist but no TGW — recommend TGW for simpler routing."""
    if (topology.get("transitGateways") or []):
        return None
    if not (topology.get("vpnGateways") or []):
        return None

    return {
        "id": "rec-no-tgw",
        "ruleId": "no-tgw",
        "category": "resiliency",
        "severity": "warning",
        "title": "Consider Using Transit Gateway",
        "description": (
            "Using a Transit Gateway instead of multiple Virtual Private "
            "Gateways simplifies routing and enables better scalability."
        ),
        "additionalNodes": [],
        "additionalEdges": [],
    }


# ----- Rule 4: single-vgw ---------------------------------------------------


def rule_single_vgw(topology: TopologyData) -> Optional[Recommendation]:
    """Exactly one VGW and no TGW — suggest a redundant VGW."""
    vpn_gws = topology.get("vpnGateways") or []
    if len(vpn_gws) != 1 or (topology.get("transitGateways") or []):
        return None

    return {
        "id": "rec-single-vgw",
        "ruleId": "single-vgw",
        "category": "resiliency",
        "severity": "warning",
        "title": "Add Redundant Virtual Private Gateway",
        "description": (
            "You have a single Virtual Private Gateway. Consider adding a "
            "second one for redundancy."
        ),
        "additionalNodes": [],
        "additionalEdges": [],
    }


# ----- Rule 5: no-lag -------------------------------------------------------


def rule_no_lag(topology: TopologyData) -> Optional[Recommendation]:
    """2+ connections at the same location but not bundled into a LAG."""
    if (topology.get("lags") or []):
        return None
    connections = topology.get("connections") or []
    if len(connections) < 2:
        return None

    location_connections: dict[str, int] = {}
    for conn in connections:
        loc = conn.get("location", "")
        location_connections[loc] = location_connections.get(loc, 0) + 1

    if not any(c >= 2 for c in location_connections.values()):
        return None

    return {
        "id": "rec-no-lag",
        "ruleId": "no-lag",
        "category": "resiliency",
        "severity": "info",
        "title": "Consider Using LAG Groups",
        "description": (
            "Link Aggregation Groups can bundle multiple connections for "
            "simplified management."
        ),
        "additionalNodes": [],
        "additionalEdges": [],
    }
