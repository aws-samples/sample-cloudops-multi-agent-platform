"""Recommendation engine — ties together resiliency + best-practice rules.

Python port of source ``dx-visualizer/src/engine/recommendation-engine.ts`` (276 lines).

Public API:
- ``analyze_topology(topology, targets)`` → CombinedAssessment
- ``get_recommended_graph(assessment, focused_dxgw_id)`` → ghost nodes + edges

Scoring math (preserved from source):
    base[level] - (critical_count × 10) - (warning_count × 5), clamped [0,100]
    base: none=0, devtest=30, high=65, maximum=100

Auto-escalation: when a DXGW already meets/exceeds the user's target, bump
the effective target one tier so recommendations still surface a next step.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from ..types import (
    CombinedAssessment,
    DxGatewayAssessment,
    Recommendation,
    ResiliencyLevel,
    ResiliencyTarget,
    TopologyData,
)
from .bestpractice_rules import (
    get_all_bestpractice_results,
    rule_connection_not_available,
    rule_enterprise_support_required,
    rule_vif_down,
    rule_well_architected_review_required,
)
from .resiliency_rules import (
    rule_no_lag,
    rule_no_tgw,
    rule_single_connection_per_location,
    rule_single_dx_location,
    rule_single_vgw,
)
from .sla_gating import get_location_device_counts

_BASE_SCORES: Dict[ResiliencyLevel, int] = {
    "none": 0,
    "devtest": 30,
    "high": 65,
    "maximum": 100,
}

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _determine_resiliency_level(topology: TopologyData) -> ResiliencyLevel:
    """Tier determination based on location + device counts.

    AWS Direct Connect SLA page defines three named deployments:
      - Multi-Site Redundant      → 99.99% (2+ locations, 2+ devices each)
      - Multi-Site Non-Redundant  → 99.9%  (2+ locations, 1+ device each)
      - Single Connection         → 95.0%  (one Connection, LAG counts as one)

    "1 location with 2+ devices" is not a named AWS tier. Strictly stronger
    than Single Connection but covered by the same 95% SLA — we keep it
    under 'devtest' alongside the single-connection case.
    """
    if not (topology.get("connections") or []) and not (
        topology.get("virtualInterfaces") or []
    ):
        return "none"

    location_devices = get_location_device_counts(topology)
    all_multi = (
        len(location_devices) > 0
        and all(c >= 2 for c in location_devices.values())
    )

    if len(location_devices) >= 2 and all_multi:
        return "maximum"
    if len(location_devices) >= 2:
        return "high"
    if len(location_devices) >= 1:
        return "devtest"
    return "none"


def _compute_score(
    level: ResiliencyLevel, recommendations: List[Recommendation]
) -> int:
    score = _BASE_SCORES[level]
    critical_count = sum(
        1 for r in recommendations if r.get("severity") == "critical"
    )
    warning_count = sum(
        1 for r in recommendations if r.get("severity") == "warning"
    )
    score -= critical_count * 10
    score -= warning_count * 5
    return max(0, min(100, score))


def _build_dxgw_scope(
    topology: TopologyData, dx_gateway_id: str
) -> TopologyData:
    """Return a topology view containing only resources that feed one DXGW.

    Walks VIF → Connection → Location so each DXGW is assessed on its own
    posture. Other top-level fields (VPCs, TGWs, Cloud WAN) pass through
    unchanged — their rule relevance is topology-wide, not per-DXGW.
    """
    scoped_vifs = [
        v
        for v in topology.get("virtualInterfaces") or []
        if v.get("directConnectGatewayId") == dx_gateway_id
    ]
    scoped_conn_ids = {
        v.get("connectionId") for v in scoped_vifs if v.get("connectionId")
    }
    scoped_conns = [
        c
        for c in topology.get("connections") or []
        if c.get("connectionId") in scoped_conn_ids
    ]

    scoped_location_codes: set[str] = set()
    for c in scoped_conns:
        if c.get("location"):
            scoped_location_codes.add(c["location"])
    for v in scoped_vifs:
        if v.get("location"):
            scoped_location_codes.add(v["location"])

    scoped_locations = [
        l
        for l in topology.get("locations") or []
        if l.get("locationCode") in scoped_location_codes
    ]
    scoped_lags = [
        lag
        for lag in topology.get("lags") or []
        if any(
            c.get("connectionId") in scoped_conn_ids
            for c in (lag.get("connections") or [])
        )
    ]

    return {
        **topology,  # type: ignore[misc]
        "connections": scoped_conns,
        "virtualInterfaces": scoped_vifs,
        "locations": scoped_locations,
        "lags": scoped_lags,
    }


def _run_per_dxgw_rules(
    scope: TopologyData,
    target: ResiliencyTarget,
    current_level: ResiliencyLevel,
    dx_gateway_id: str,
    dx_gateway_name: Optional[str],
) -> List[Recommendation]:
    recs: List[Recommendation] = []

    single_loc = rule_single_dx_location(
        scope, target, dx_gateway_id, dx_gateway_name
    )
    if single_loc:
        recs.append(single_loc)

    recs.extend(
        rule_single_connection_per_location(scope, target, dx_gateway_id)
    )

    no_lag = rule_no_lag(scope)
    if no_lag:
        recs.append(no_lag)

    vif_down_r = rule_vif_down(scope)
    if vif_down_r.recommendation:
        recs.append(vif_down_r.recommendation)

    conn_down_r = rule_connection_not_available(scope)
    if conn_down_r.recommendation:
        recs.append(conn_down_r.recommendation)

    # SLA-precondition attestations (tier-dependent)
    ent_r = rule_enterprise_support_required(scope, current_level, target)
    if ent_r.recommendation:
        recs.append(ent_r.recommendation)

    war_r = rule_well_architected_review_required(
        scope, current_level, target
    )
    if war_r.recommendation:
        recs.append(war_r.recommendation)

    recs.sort(key=lambda r: _SEVERITY_ORDER.get(r.get("severity", ""), 3))
    return recs


def analyze_topology(
    topology: TopologyData,
    targets: Union[
        Dict[str, ResiliencyTarget], ResiliencyTarget
    ] = "high",
) -> CombinedAssessment:
    """Main entry point for assessment.

    ``targets`` can be either a scalar (applies to every DXGW and global view)
    or a dict keyed by DXGW ID. Each DXGW auto-escalates past tiers it already
    meets so recommendations always surface a next step.
    """
    top_level = _determine_resiliency_level(topology)

    def resolve_target(dxgw_id: str) -> ResiliencyTarget:
        if isinstance(targets, str):
            return targets
        return targets.get(dxgw_id, "high")

    # --- Per-DXGW assessments ---
    per_dxgw: List[DxGatewayAssessment] = []
    for gw in topology.get("dxGateways") or []:
        gw_id = gw.get("directConnectGatewayId", "")
        scope = _build_dxgw_scope(topology, gw_id)
        level = _determine_resiliency_level(scope)
        user_target = resolve_target(gw_id)
        # Auto-escalate when the DXGW already meets/exceeds the user's pick.
        if level == "maximum":
            effective_target: ResiliencyTarget = "maximum"
        elif level == "high":
            effective_target = "maximum"
        else:
            effective_target = user_target

        recs = _run_per_dxgw_rules(
            scope,
            effective_target,
            level,
            gw_id,
            gw.get("directConnectGatewayName"),
        )

        scoped_conns = scope.get("connections") or []
        scoped_vifs = scope.get("virtualInterfaces") or []
        loc_codes_from_conns = {
            c.get("location") for c in scoped_conns if c.get("location")
        }
        if not loc_codes_from_conns:
            loc_codes_from_conns = {
                v.get("location") for v in scoped_vifs if v.get("location")
            }
        location_count = len(loc_codes_from_conns)

        # A DXGW is "unattached" (for resiliency purposes) when it has no
        # VIFs — no VIFs means no DX location/connection path, so SLA
        # tiering is meaningless regardless of whether the DXGW has TGW /
        # VGW associations on the AWS side. Consumers of this flag (bulk
        # picker, per-DXGW card) want to skip any gateway whose resiliency
        # posture can't change because there's nothing on the DX side to
        # make redundant.
        has_vif = any(
            v.get("directConnectGatewayId") == gw_id
            for v in topology.get("virtualInterfaces") or []
        )
        has_association = any(
            a.get("directConnectGatewayId") == gw_id
            for a in topology.get("dxGatewayAssociations") or []
        )
        is_unattached = not has_vif

        per_dxgw.append(
            {
                "dxGatewayId": gw_id,
                "dxGatewayName": gw.get("directConnectGatewayName") or gw_id,
                "currentLevel": level,
                "targetLevel": effective_target,
                "score": _compute_score(level, recs),
                "locationCount": location_count,
                "connectionCount": len(scoped_conns),
                "isUnattached": is_unattached,
                "hasVif": has_vif,
                "hasAssociation": has_association,
                "recommendations": recs,
            }
        )

    # --- Global rules (not tied to a specific DXGW) ---
    global_resiliency_recs: List[Recommendation] = []
    no_tgw = rule_no_tgw(topology)
    if no_tgw:
        global_resiliency_recs.append(no_tgw)
    single_vgw = rule_single_vgw(topology)
    if single_vgw:
        global_resiliency_recs.append(single_vgw)

    # Fallback target for aggregate view: first DXGW's target, or scalar.
    if isinstance(targets, str):
        global_target: ResiliencyTarget = targets
    else:
        dx_gws = topology.get("dxGateways") or []
        global_target = (
            targets.get(dx_gws[0].get("directConnectGatewayId", ""), "high")
            if dx_gws
            else "high"
        )
    if top_level == "maximum":
        global_effective_target: ResiliencyTarget = "maximum"
    elif top_level == "high":
        global_effective_target = "maximum"
    else:
        global_effective_target = global_target

    # No DXGWs at all — still run resiliency rules at the global level so
    # test fixtures / edge cases get useful recommendations.
    if not (topology.get("dxGateways") or []):
        single_loc = rule_single_dx_location(topology, global_effective_target)
        if single_loc:
            global_resiliency_recs.append(single_loc)
        global_resiliency_recs.extend(
            rule_single_connection_per_location(topology, global_effective_target)
        )
        no_lag = rule_no_lag(topology)
        if no_lag:
            global_resiliency_recs.append(no_lag)

    bp_result = get_all_bestpractice_results(topology)
    # VIF-down and connection-not-available surface per-DXGW now; drop from global.
    global_bp_recs = [
        r
        for r in bp_result["recommendations"]
        if r.get("ruleId") not in ("vif-down", "connection-not-available")
    ]

    # --- Aggregated views (back-compat) ---
    per_dxgw_resiliency_recs: List[Recommendation] = []
    per_dxgw_bp_recs: List[Recommendation] = []
    for d in per_dxgw:
        for r in d.get("recommendations") or []:
            if r.get("category") == "resiliency":
                per_dxgw_resiliency_recs.append(r)
            elif r.get("category") == "bestpractice":
                per_dxgw_bp_recs.append(r)
    aggregate_resiliency = per_dxgw_resiliency_recs + global_resiliency_recs
    aggregate_bp = per_dxgw_bp_recs + global_bp_recs

    # Source uses "global" as a field name — reserved in Python, so we
    # construct the dict explicitly and stick it under the literal key.
    result: Dict[str, Any] = {
        "perDxGateway": per_dxgw,
        "global": {
            "resiliency": {
                "currentLevel": top_level,
                "targetLevel": global_effective_target,
                "score": _compute_score(top_level, global_resiliency_recs),
                "recommendations": global_resiliency_recs,
            },
            "bestPractice": {
                "annotations": bp_result["annotations"],
                "recommendations": global_bp_recs,
            },
        },
        "resiliency": {
            "currentLevel": top_level,
            "targetLevel": global_effective_target,
            "score": _compute_score(top_level, aggregate_resiliency),
            "recommendations": aggregate_resiliency,
        },
        "bestPractice": {
            "annotations": bp_result["annotations"],
            "recommendations": aggregate_bp,
        },
    }
    return result  # type: ignore[return-value]


def get_recommended_graph(
    assessment: CombinedAssessment,
    focused_dx_gateway_id: Optional[str] = None,
) -> Dict[str, List[Any]]:
    """Return ghost nodes + edges to overlay on the current-view graph.

    When focused on a specific DXGW, walks that gateway's recommendations
    only. Otherwise returns the aggregated resiliency ghosts.
    """
    nodes: List[Any] = []
    edges: List[Any] = []

    if focused_dx_gateway_id:
        match = next(
            (
                g
                for g in assessment.get("perDxGateway") or []
                if g.get("dxGatewayId") == focused_dx_gateway_id
            ),
            None,
        )
        recs = (
            [
                r
                for r in (match.get("recommendations") or [])
                if r.get("category") == "resiliency"
            ]
            if match
            else []
        )
        for rec in recs:
            nodes.extend(rec.get("additionalNodes") or [])
            edges.extend(rec.get("additionalEdges") or [])
        return {"nodes": nodes, "edges": edges}

    for rec in (assessment.get("resiliency") or {}).get(
        "recommendations"
    ) or []:
        nodes.extend(rec.get("additionalNodes") or [])
        edges.extend(rec.get("additionalEdges") or [])
    return {"nodes": nodes, "edges": edges}
