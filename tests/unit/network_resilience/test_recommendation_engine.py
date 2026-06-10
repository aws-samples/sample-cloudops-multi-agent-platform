"""Pytest port of source ``engine/__tests__/recommendation-engine.test.ts``."""

from __future__ import annotations

from network_resilience.engine.recommendation_engine import (
    analyze_topology,
    get_recommended_graph,
)

from .helpers import conn, make_empty_topology


# ---------- analyze_topology ----------


def test_none_level_for_empty_topology() -> None:
    result = analyze_topology(make_empty_topology())
    assert result["resiliency"]["currentLevel"] == "none"
    assert result["resiliency"]["score"] == 0


def test_devtest_for_single_conn_single_location() -> None:
    t = make_empty_topology()
    t["connections"] = [conn(connectionId="c1", location="EqDC2")]
    result = analyze_topology(t)
    assert result["resiliency"]["currentLevel"] == "devtest"
    assert result["resiliency"]["targetLevel"] == "high"


def test_honors_explicit_maximum_target_from_devtest() -> None:
    t = make_empty_topology()
    t["connections"] = [conn(connectionId="c1", location="EqDC2")]
    result = analyze_topology(t, "maximum")
    assert result["resiliency"]["currentLevel"] == "devtest"
    assert result["resiliency"]["targetLevel"] == "maximum"


def test_devtest_for_two_conns_same_location() -> None:
    t = make_empty_topology()
    t["connections"] = [
        conn(connectionId="c1", location="EqDC2"),
        conn(connectionId="c2", location="EqDC2"),
    ]
    result = analyze_topology(t)
    assert result["resiliency"]["currentLevel"] == "devtest"
    assert result["resiliency"]["targetLevel"] == "high"


def test_high_for_two_locations_single_conn_each_target_maximum() -> None:
    """From 'high', auto-escalate target to 'maximum'."""
    t = make_empty_topology()
    t["connections"] = [
        conn(connectionId="c1", location="EqDC2"),
        conn(connectionId="c2", location="EqDC6"),
    ]
    result = analyze_topology(t)
    assert result["resiliency"]["currentLevel"] == "high"
    assert result["resiliency"]["targetLevel"] == "maximum"


def test_maximum_for_two_locations_two_conns_each() -> None:
    t = make_empty_topology()
    t["connections"] = [
        conn(connectionId="c1", location="EqDC2"),
        conn(connectionId="c2", location="EqDC2"),
        conn(connectionId="c3", location="EqDC6"),
        conn(connectionId="c4", location="EqDC6"),
    ]
    result = analyze_topology(t)
    assert result["resiliency"]["currentLevel"] == "maximum"
    assert result["resiliency"]["targetLevel"] == "maximum"


def test_not_maximum_when_two_conns_share_device() -> None:
    """Both connections at EqDC6 terminate on same AWS router — device
    redundancy isn't met even though raw connection count is 2.
    """
    t = make_empty_topology()
    t["connections"] = [
        conn(connectionId="c1", location="EqDC2", awsLogicalDeviceId="dev-A"),
        conn(connectionId="c2", location="EqDC2", awsLogicalDeviceId="dev-B"),
        conn(connectionId="c3", location="EqDC6", awsLogicalDeviceId="dev-C"),
        conn(connectionId="c4", location="EqDC6", awsLogicalDeviceId="dev-C"),
    ]
    result = analyze_topology(t, "maximum")
    assert result["resiliency"]["currentLevel"] == "high"
    rule_ids = [r["ruleId"] for r in result["resiliency"]["recommendations"]]
    assert "single-connection-per-location" in rule_ids
    # Device-short location should be EqDC6, not EqDC2
    rec = next(
        r
        for r in result["resiliency"]["recommendations"]
        if r["ruleId"] == "single-connection-per-location"
    )
    assert "EqDC6" in rec["title"]


def test_maximum_when_two_conns_per_location_distinct_devices() -> None:
    t = make_empty_topology()
    t["connections"] = [
        conn(connectionId="c1", location="EqDC2", awsLogicalDeviceId="dev-A"),
        conn(connectionId="c2", location="EqDC2", awsLogicalDeviceId="dev-B"),
        conn(connectionId="c3", location="EqDC6", awsLogicalDeviceId="dev-C"),
        conn(connectionId="c4", location="EqDC6", awsLogicalDeviceId="dev-D"),
    ]
    result = analyze_topology(t)
    assert result["resiliency"]["currentLevel"] == "maximum"


def test_falls_back_to_connection_id_when_aws_logical_missing() -> None:
    """Hosted-VIF accounts may not expose awsLogicalDeviceId. Fall back to
    connection ID so each raw conn counts as its own device.
    """
    t = make_empty_topology()
    t["connections"] = [
        conn(connectionId="c1", location="EqDC2"),
        conn(connectionId="c2", location="EqDC2"),
        conn(connectionId="c3", location="EqDC6"),
        conn(connectionId="c4", location="EqDC6"),
    ]
    result = analyze_topology(t)
    assert result["resiliency"]["currentLevel"] == "maximum"


def test_single_dx_location_rec_emitted_default_high_target() -> None:
    t = make_empty_topology()
    t["connections"] = [conn(connectionId="c1", location="EqDC2")]
    result = analyze_topology(t)
    rule_ids = [r["ruleId"] for r in result["resiliency"]["recommendations"]]
    assert "single-dx-location" in rule_ids
    # High target doesn't generate per-location redundancy recs
    assert "single-connection-per-location" not in rule_ids


def test_single_connection_per_location_rec_emitted_maximum_target() -> None:
    t = make_empty_topology()
    t["connections"] = [conn(connectionId="c1", location="EqDC2")]
    result = analyze_topology(t, "maximum")
    rule_ids = [r["ruleId"] for r in result["resiliency"]["recommendations"]]
    assert "single-dx-location" in rule_ids
    assert "single-connection-per-location" in rule_ids


def test_includes_best_practice_recommendations() -> None:
    t = make_empty_topology()
    t["connections"] = [conn(connectionId="c1", location="EqDC2")]
    result = analyze_topology(t)
    assert len(result["bestPractice"]["recommendations"]) > 0
    bp_ids = [r["ruleId"] for r in result["bestPractice"]["recommendations"]]
    assert "bfd-guidance" in bp_ids
    assert "no-vpn-backup" in bp_ids


def test_score_decreases_with_critical_and_warning_recs() -> None:
    t = make_empty_topology()
    t["connections"] = [
        conn(connectionId="c1", location="EqDC2"),
        conn(connectionId="c2", location="EqDC2"),
        conn(connectionId="c3", location="EqDC6"),
        conn(connectionId="c4", location="EqDC6"),
    ]
    max_result = analyze_topology(t)
    assert max_result["resiliency"]["score"] == 100

    t2 = make_empty_topology()
    t2["connections"] = [conn(connectionId="c1", location="EqDC2")]
    low_result = analyze_topology(t2)
    assert (
        low_result["resiliency"]["score"] < max_result["resiliency"]["score"]
    )


# ---------- get_recommended_graph ----------


def test_recommended_graph_collects_ghosts_from_resiliency_recs() -> None:
    t = make_empty_topology()
    t["connections"] = [conn(connectionId="c1", location="EqDC2")]
    assessment = analyze_topology(t)
    result = get_recommended_graph(assessment)
    assert len(result["nodes"]) > 0
    assert len(result["edges"]) > 0
    # All ghost nodes marked as recommended
    for n in result["nodes"]:
        assert n.get("isRecommended") is True


def test_independent_per_dxgw_assessments() -> None:
    t = make_empty_topology()
    t["dxGateways"] = [
        {
            "directConnectGatewayId": "gw-healthy",
            "directConnectGatewayName": "DxGwHealthy",
            "amazonSideAsn": 64512,
            "directConnectGatewayState": "available",
        },
        {
            "directConnectGatewayId": "gw-single",
            "directConnectGatewayName": "DxGwOsaka",
            "amazonSideAsn": 64512,
            "directConnectGatewayState": "available",
        },
    ]
    t["connections"] = [
        conn(connectionId="c1", location="EqDC2"),
        conn(connectionId="c2", location="EqDC6"),
        conn(connectionId="c3", location="Osaka1"),
    ]
    t["virtualInterfaces"] = [
        {
            "virtualInterfaceId": "v1",
            "connectionId": "c1",
            "location": "EqDC2",
            "directConnectGatewayId": "gw-healthy",
            "virtualInterfaceState": "available",
            "bgpPeers": [{"bgpStatus": "up"}],
        },
        {
            "virtualInterfaceId": "v2",
            "connectionId": "c2",
            "location": "EqDC6",
            "directConnectGatewayId": "gw-healthy",
            "virtualInterfaceState": "available",
            "bgpPeers": [{"bgpStatus": "up"}],
        },
        {
            "virtualInterfaceId": "v3",
            "connectionId": "c3",
            "location": "Osaka1",
            "directConnectGatewayId": "gw-single",
            "virtualInterfaceState": "available",
            "bgpPeers": [{"bgpStatus": "up"}],
        },
    ]

    result = analyze_topology(t)
    assert len(result["perDxGateway"]) == 2

    healthy = next(
        g for g in result["perDxGateway"] if g["dxGatewayId"] == "gw-healthy"
    )
    single = next(
        g for g in result["perDxGateway"] if g["dxGatewayId"] == "gw-single"
    )

    assert healthy["currentLevel"] == "high"
    healthy_rule_ids = [r["ruleId"] for r in healthy["recommendations"]]
    assert "single-dx-location" not in healthy_rule_ids

    assert single["currentLevel"] == "devtest"
    single_rule_ids = [r["ruleId"] for r in single["recommendations"]]
    assert "single-dx-location" in single_rule_ids
    assert single["score"] < healthy["score"]


def test_global_section_separate_from_per_dxgw() -> None:
    t = make_empty_topology()
    t["dxGateways"] = [
        {
            "directConnectGatewayId": "gw1",
            "directConnectGatewayName": "Dx",
            "amazonSideAsn": 64512,
            "directConnectGatewayState": "available",
        }
    ]
    t["connections"] = [
        conn(connectionId="c1", location="EqDC2"),
        conn(connectionId="c2", location="EqDC6"),
    ]
    t["virtualInterfaces"] = [
        {
            "virtualInterfaceId": "v1",
            "connectionId": "c1",
            "location": "EqDC2",
            "directConnectGatewayId": "gw1",
            "virtualInterfaceState": "available",
            "bgpPeers": [{"bgpStatus": "up"}],
        },
        {
            "virtualInterfaceId": "v2",
            "connectionId": "c2",
            "location": "EqDC6",
            "directConnectGatewayId": "gw1",
            "virtualInterfaceState": "available",
            "bgpPeers": [{"bgpStatus": "up"}],
        },
    ]

    result = analyze_topology(t)
    global_bp_ids = [
        r["ruleId"] for r in result["global"]["bestPractice"]["recommendations"]
    ]
    assert "bfd-guidance" in global_bp_ids
    assert "no-vpn-backup" in global_bp_ids
    # VIF-down is per-DXGW now; must not leak into global
    assert "vif-down" not in global_bp_ids


def test_recommended_graph_empty_when_no_ghost_recs() -> None:
    t = make_empty_topology()
    t["connections"] = [
        conn(connectionId="c1", location="EqDC2"),
        conn(connectionId="c2", location="EqDC2"),
        conn(connectionId="c3", location="EqDC6"),
        conn(connectionId="c4", location="EqDC6"),
    ]
    assessment = analyze_topology(t)
    result = get_recommended_graph(assessment)
    assert result["nodes"] == []
    assert result["edges"] == []
