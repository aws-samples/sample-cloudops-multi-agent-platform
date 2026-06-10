"""Pytest port of source ``engine/__tests__/bestpractice-rules.test.ts``."""

from __future__ import annotations

from network_resilience.engine.bestpractice_rules import (
    get_all_bestpractice_results,
    rule_bfd_guidance,
    rule_bgp_route_limit,
    rule_bgp_timers_fallback,
    rule_cgw_redundancy,
    rule_connection_not_available,
    rule_consistent_prefix_advertisement,
    rule_cross_region_path,
    rule_dx_failover_testing,
    rule_dx_location_redundancy,
    rule_dx_partner_diversity,
    rule_failover_runbooks,
    rule_no_vpn_backup,
    rule_resiliency_toolkit,
    rule_sla_awareness,
    rule_vif_down,
    rule_vpn_dpd,
    rule_vpn_tunnel_redundancy,
)

from .helpers import make_empty_topology


# ---------- rule_bfd_guidance ----------


def test_bfd_returns_none_for_empty() -> None:
    assert rule_bfd_guidance(make_empty_topology()).recommendation is None


def test_bfd_returns_guidance_when_connections_exist() -> None:
    t = make_empty_topology()
    t["connections"] = [{"connectionId": "c1", "connectionState": "available"}]
    r = rule_bfd_guidance(t)
    assert r.recommendation is not None
    assert r.recommendation["ruleId"] == "bfd-guidance"


def test_bfd_returns_guidance_when_only_vifs_exist() -> None:
    t = make_empty_topology()
    t["virtualInterfaces"] = [{"virtualInterfaceId": "v1", "bgpPeers": []}]
    assert rule_bfd_guidance(t).recommendation is not None


# ---------- rule_vif_down ----------


def test_vif_down_none_when_all_available() -> None:
    t = make_empty_topology()
    t["virtualInterfaces"] = [
        {
            "virtualInterfaceId": "v1",
            "virtualInterfaceName": "vif-1",
            "virtualInterfaceState": "available",
            "bgpPeers": [{"bgpStatus": "up"}],
        }
    ]
    assert rule_vif_down(t).recommendation is None


def test_vif_down_detects_non_available_state() -> None:
    t = make_empty_topology()
    t["virtualInterfaces"] = [
        {
            "virtualInterfaceId": "v1",
            "virtualInterfaceName": "vif-down",
            "virtualInterfaceState": "down",
            "bgpPeers": [],
        }
    ]
    r = rule_vif_down(t)
    assert r.recommendation is not None
    assert r.recommendation["severity"] == "critical"
    assert "vif-down" in r.recommendation["description"]


def test_vif_down_detects_all_bgp_peers_down() -> None:
    t = make_empty_topology()
    t["virtualInterfaces"] = [
        {
            "virtualInterfaceId": "v1",
            "virtualInterfaceName": "vif-bgp-down",
            "virtualInterfaceState": "available",
            "bgpPeers": [{"bgpStatus": "down"}, {"bgpStatus": "down"}],
        }
    ]
    r = rule_vif_down(t)
    assert r.recommendation is not None
    assert "vif-bgp-down" in r.recommendation["description"]


# ---------- rule_connection_not_available ----------


def test_conn_not_avail_none_when_all_available() -> None:
    t = make_empty_topology()
    t["connections"] = [
        {
            "connectionId": "c1",
            "connectionName": "conn-1",
            "connectionState": "available",
        }
    ]
    assert rule_connection_not_available(t).recommendation is None


def test_conn_not_avail_detects_down() -> None:
    t = make_empty_topology()
    t["connections"] = [
        {
            "connectionId": "c1",
            "connectionName": "bad-conn",
            "connectionState": "down",
        }
    ]
    r = rule_connection_not_available(t)
    assert r.recommendation is not None
    assert r.recommendation["severity"] == "critical"
    assert "bad-conn" in r.recommendation["description"]


# ---------- rule_no_vpn_backup ----------


def test_no_vpn_backup_none_when_empty() -> None:
    assert rule_no_vpn_backup(make_empty_topology()).recommendation is None


def test_no_vpn_backup_recommends_when_dx_but_no_vpn() -> None:
    t = make_empty_topology()
    t["connections"] = [{"connectionId": "c1", "connectionState": "available"}]
    r = rule_no_vpn_backup(t)
    assert r.recommendation is not None
    assert r.recommendation["ruleId"] == "no-vpn-backup"
    assert r.recommendation["severity"] == "warning"


def test_no_vpn_backup_silent_when_vpn_exists() -> None:
    t = make_empty_topology()
    t["connections"] = [{"connectionId": "c1", "connectionState": "available"}]
    t["vpnConnections"] = [{"vpnConnectionId": "vpn-1"}]
    assert rule_no_vpn_backup(t).recommendation is None


# ---------- rule_cross_region_path ----------


def test_cross_region_none_when_no_connections() -> None:
    assert rule_cross_region_path(make_empty_topology()).recommendation is None


def test_cross_region_none_when_no_resource_regions() -> None:
    t = make_empty_topology()
    t["connections"] = [
        {"connectionId": "c1", "region": "us-east-1", "connectionState": "available"}
    ]
    assert rule_cross_region_path(t).recommendation is None


def test_cross_region_none_when_regions_match() -> None:
    t = make_empty_topology()
    t["connections"] = [
        {"connectionId": "c1", "region": "us-east-1", "connectionState": "available"}
    ]
    t["vpcs"] = [{"vpcId": "vpc-1", "region": "us-east-1", "cidrBlock": "10.0.0.0/16"}]
    assert rule_cross_region_path(t).recommendation is None


def test_cross_region_detects_when_vpc_region_differs() -> None:
    t = make_empty_topology()
    t["connections"] = [
        {"connectionId": "c1", "region": "us-east-1", "connectionState": "available"}
    ]
    t["vpcs"] = [
        {"vpcId": "vpc-1", "region": "ap-southeast-1", "cidrBlock": "10.0.0.0/16"}
    ]
    r = rule_cross_region_path(t)
    assert r.recommendation is not None
    assert r.recommendation["ruleId"] == "cross-region-path"
    assert r.recommendation["severity"] == "info"
    assert "ap-southeast-1" in r.recommendation["description"]


def test_cross_region_detects_when_tgw_region_differs() -> None:
    t = make_empty_topology()
    t["connections"] = [
        {"connectionId": "c1", "region": "us-east-1", "connectionState": "available"}
    ]
    t["transitGateways"] = [
        {
            "transitGatewayId": "tgw-1",
            "transitGatewayArn": "arn:aws:ec2:eu-west-1:123:transit-gateway/tgw-1",
        }
    ]
    r = rule_cross_region_path(t)
    assert r.recommendation is not None
    assert "eu-west-1" in r.recommendation["description"]


# ---------- rule_sla_awareness / rule_resiliency_toolkit ----------


def test_sla_awareness_none_for_empty() -> None:
    assert rule_sla_awareness(make_empty_topology()).recommendation is None


def test_sla_awareness_recommends_when_dx_exists() -> None:
    t = make_empty_topology()
    t["connections"] = [{"connectionId": "c1", "connectionState": "available"}]
    r = rule_sla_awareness(t)
    assert r.recommendation is not None
    assert r.recommendation["ruleId"] == "sla-awareness"
    assert r.recommendation["severity"] == "info"
    assert "aws.amazon.com/directconnect/sla" in r.recommendation["description"]


def test_resiliency_toolkit_none_for_empty() -> None:
    assert rule_resiliency_toolkit(make_empty_topology()).recommendation is None


def test_resiliency_toolkit_when_dx_exists() -> None:
    t = make_empty_topology()
    t["virtualInterfaces"] = [{"virtualInterfaceId": "v1", "bgpPeers": []}]
    r = rule_resiliency_toolkit(t)
    assert r.recommendation is not None
    assert r.recommendation["ruleId"] == "resiliency-toolkit"
    assert "resiliency_toolkit" in r.recommendation["description"]


# ---------- rule_consistent_prefix_advertisement ----------


def test_consistent_prefix_none_fewer_than_two() -> None:
    t = make_empty_topology()
    t["virtualInterfaces"] = [{"virtualInterfaceId": "v1", "bgpPeers": []}]
    assert rule_consistent_prefix_advertisement(t).recommendation is None


def test_consistent_prefix_when_two_plus_vifs() -> None:
    t = make_empty_topology()
    t["virtualInterfaces"] = [
        {"virtualInterfaceId": "v1", "bgpPeers": []},
        {"virtualInterfaceId": "v2", "bgpPeers": []},
    ]
    r = rule_consistent_prefix_advertisement(t)
    assert r.recommendation is not None
    assert r.recommendation["ruleId"] == "consistent-prefix-advertisement"


# ---------- rule_bgp_route_limit ----------


def _make_vif(vid: str, vif_type: str = "private"):
    return {
        "virtualInterfaceId": vid,
        "virtualInterfaceName": vid,
        "virtualInterfaceType": vif_type,
        "bgpPeers": [],
    }


def test_bgp_limit_none_without_applicable_vifs() -> None:
    assert rule_bgp_route_limit(make_empty_topology()).recommendation is None


def test_bgp_limit_ignores_public_vifs() -> None:
    t = make_empty_topology()
    t["virtualInterfaces"] = [_make_vif("v-pub", "public")]
    assert rule_bgp_route_limit(t).recommendation is None


def test_bgp_limit_info_guidance_when_metrics_missing() -> None:
    t = make_empty_topology()
    t["virtualInterfaces"] = [_make_vif("v1")]
    r = rule_bgp_route_limit(t)
    assert r.recommendation is not None
    assert r.recommendation["ruleId"] == "bgp-route-limit"
    assert r.recommendation["severity"] == "info"
    assert "limits.html" in r.recommendation["description"]


def test_bgp_limit_met_when_well_under() -> None:
    t = make_empty_topology()
    t["virtualInterfaces"] = [_make_vif("v1"), _make_vif("v2", "transit")]
    t["bgpPrefixMetrics"] = {
        "v1": {"accepted": 12, "advertised": 5},
        "v2": {"accepted": 34, "advertised": 8},
    }
    r = rule_bgp_route_limit(t)
    assert r.recommendation is not None
    assert r.recommendation["ruleId"] == "bgp-route-limit-ok"
    assert r.recommendation["severity"] == "info"
    assert "peak observed is 34" in r.recommendation["description"]


def test_bgp_limit_warns_near_limit() -> None:
    t = make_empty_topology()
    t["virtualInterfaces"] = [_make_vif("v-near")]
    t["bgpPrefixMetrics"] = {"v-near": {"accepted": 85}}
    r = rule_bgp_route_limit(t)
    assert r.recommendation is not None
    assert r.recommendation["severity"] == "warning"
    assert "v-near" in r.recommendation["description"]
    assert "85" in r.recommendation["description"]


def test_bgp_limit_critical_over_limit() -> None:
    t = make_empty_topology()
    t["virtualInterfaces"] = [_make_vif("v-over")]
    t["bgpPrefixMetrics"] = {"v-over": {"accepted": 102}}
    r = rule_bgp_route_limit(t)
    assert r.recommendation is not None
    assert r.recommendation["severity"] == "critical"
    assert "v-over" in r.recommendation["description"]
    assert "102 accepted" in r.recommendation["description"]


# ---------- rule_vpn_tunnel_redundancy ----------


def test_vpn_tunnel_none_when_no_connections() -> None:
    assert (
        rule_vpn_tunnel_redundancy(make_empty_topology()).recommendation is None
    )


def test_vpn_tunnel_none_when_all_up() -> None:
    t = make_empty_topology()
    t["vpnConnections"] = [
        {
            "vpnConnectionId": "vpn-1",
            "customerGatewayId": "cgw-1",
            "tunnels": [{"status": "UP"}, {"status": "UP"}],
        }
    ]
    assert rule_vpn_tunnel_redundancy(t).recommendation is None


def test_vpn_tunnel_detects_down() -> None:
    t = make_empty_topology()
    t["vpnConnections"] = [
        {
            "vpnConnectionId": "vpn-degraded",
            "customerGatewayId": "cgw-1",
            "tunnels": [{"status": "UP"}, {"status": "DOWN"}],
        }
    ]
    r = rule_vpn_tunnel_redundancy(t)
    assert r.recommendation is not None
    assert r.recommendation["severity"] == "warning"
    assert "vpn-degraded" in r.recommendation["description"]


# ---------- rule_cgw_redundancy ----------


def test_cgw_none_without_vpns() -> None:
    assert rule_cgw_redundancy(make_empty_topology()).recommendation is None


def test_cgw_fires_when_all_share_same_cgw() -> None:
    t = make_empty_topology()
    t["vpnConnections"] = [
        {"vpnConnectionId": "vpn-1", "customerGatewayId": "cgw-1", "tunnels": []},
        {"vpnConnectionId": "vpn-2", "customerGatewayId": "cgw-1", "tunnels": []},
    ]
    r = rule_cgw_redundancy(t)
    assert r.recommendation is not None
    assert r.recommendation["severity"] == "warning"


def test_cgw_none_when_two_plus_cgws() -> None:
    t = make_empty_topology()
    t["vpnConnections"] = [
        {"vpnConnectionId": "vpn-1", "customerGatewayId": "cgw-1", "tunnels": []},
        {"vpnConnectionId": "vpn-2", "customerGatewayId": "cgw-2", "tunnels": []},
    ]
    assert rule_cgw_redundancy(t).recommendation is None


# ---------- rule_dx_partner_diversity ----------


def test_partner_diversity_none_fewer_than_two_conns() -> None:
    t = make_empty_topology()
    t["connections"] = [{"connectionId": "c1", "partnerName": "Equinix"}]
    assert rule_dx_partner_diversity(t).recommendation is None


def test_partner_diversity_silent_when_no_partner_names() -> None:
    t = make_empty_topology()
    t["connections"] = [
        {"connectionId": "c1"},
        {"connectionId": "c2"},
    ]
    assert rule_dx_partner_diversity(t).recommendation is None


def test_partner_diversity_fires_when_all_same() -> None:
    t = make_empty_topology()
    t["connections"] = [
        {"connectionId": "c1", "partnerName": "Equinix"},
        {"connectionId": "c2", "partnerName": "Equinix"},
    ]
    r = rule_dx_partner_diversity(t)
    assert r.recommendation is not None
    assert "Equinix" in r.recommendation["description"]


def test_partner_diversity_silent_when_different() -> None:
    t = make_empty_topology()
    t["connections"] = [
        {"connectionId": "c1", "partnerName": "Equinix"},
        {"connectionId": "c2", "partnerName": "Megaport"},
    ]
    assert rule_dx_partner_diversity(t).recommendation is None


# ---------- rule_vpn_dpd ----------


def test_vpn_dpd_none_without_connections() -> None:
    assert rule_vpn_dpd(make_empty_topology()).recommendation is None


def test_vpn_dpd_info_when_all_configured() -> None:
    t = make_empty_topology()
    t["vpnConnections"] = [
        {
            "vpnConnectionId": "vpn-1",
            "customerGatewayId": "cgw-1",
            "tunnels": [
                {"status": "UP", "dpdTimeoutAction": "restart"},
                {"status": "UP", "dpdTimeoutAction": "clear"},
            ],
        }
    ]
    r = rule_vpn_dpd(t)
    assert r.recommendation is not None
    assert r.recommendation["ruleId"] == "vpn-dpd"
    assert r.recommendation["severity"] == "info"


def test_vpn_dpd_warns_when_none_action() -> None:
    t = make_empty_topology()
    t["vpnConnections"] = [
        {
            "vpnConnectionId": "vpn-lax",
            "customerGatewayId": "cgw-1",
            "tunnels": [
                {"status": "UP", "outsideIpAddress": "1.2.3.4", "dpdTimeoutAction": "none"},
                {"status": "UP", "outsideIpAddress": "1.2.3.5", "dpdTimeoutAction": "restart"},
            ],
        }
    ]
    r = rule_vpn_dpd(t)
    assert r.recommendation is not None
    assert r.recommendation["severity"] == "warning"
    assert "vpn-lax" in r.recommendation["description"]
    assert "1.2.3.4" in r.recommendation["description"]


def test_vpn_dpd_info_when_no_dpd_populated() -> None:
    t = make_empty_topology()
    t["vpnConnections"] = [
        {
            "vpnConnectionId": "vpn-unknown",
            "customerGatewayId": "cgw-1",
            "tunnels": [{"status": "UP"}, {"status": "UP"}],
        }
    ]
    r = rule_vpn_dpd(t)
    assert r.recommendation is not None
    assert r.recommendation["severity"] == "info"


# ---------- rule_dx_location_redundancy ----------


def test_dx_location_redundancy_none_for_empty() -> None:
    assert rule_dx_location_redundancy(make_empty_topology()).recommendation is None


def test_dx_location_redundancy_info_when_dx_exists() -> None:
    t = make_empty_topology()
    t["connections"] = [{"connectionId": "c1"}]
    r = rule_dx_location_redundancy(t)
    assert r.recommendation is not None
    assert "Metro" in r.recommendation["description"]
    assert "Geographic" in r.recommendation["description"]


# ---------- rule_bgp_timers_fallback ----------


def test_bgp_timers_none_without_vifs() -> None:
    assert rule_bgp_timers_fallback(make_empty_topology()).recommendation is None


def test_bgp_timers_info_with_vifs() -> None:
    t = make_empty_topology()
    t["virtualInterfaces"] = [{"virtualInterfaceId": "v1", "bgpPeers": []}]
    r = rule_bgp_timers_fallback(t)
    assert r.recommendation is not None
    assert r.recommendation["ruleId"] == "bgp-timers-fallback"


# ---------- rule_dx_failover_testing ----------


def test_failover_testing_none_for_empty() -> None:
    assert rule_dx_failover_testing(make_empty_topology()).recommendation is None


def test_failover_testing_info_when_dx_exists() -> None:
    t = make_empty_topology()
    t["connections"] = [{"connectionId": "c1"}]
    r = rule_dx_failover_testing(t)
    assert r.recommendation is not None
    assert "72 hours" in r.recommendation["description"]


# ---------- rule_failover_runbooks ----------


def test_runbooks_none_for_empty() -> None:
    assert rule_failover_runbooks(make_empty_topology()).recommendation is None


def test_runbooks_info_when_dx_exists() -> None:
    t = make_empty_topology()
    t["connections"] = [{"connectionId": "c1"}]
    r = rule_failover_runbooks(t)
    assert r.recommendation is not None
    assert r.recommendation["ruleId"] == "failover-runbooks"


# ---------- get_all_bestpractice_results ----------


def test_aggregator_sorts_by_severity() -> None:
    t = make_empty_topology()
    t["connections"] = [
        {"connectionId": "c1", "connectionName": "bad", "connectionState": "down"}
    ]
    result = get_all_bestpractice_results(t)
    recs = result["recommendations"]
    severities = [r["severity"] for r in recs]
    # Critical must come first, severities must be non-decreasing
    assert severities[0] == "critical"
    assert "warning" in severities
    assert severities.count("info") > 0
    order = {"critical": 0, "warning": 1, "info": 2}
    for i in range(1, len(severities)):
        assert order[severities[i]] >= order[severities[i - 1]]


def test_aggregator_empty_for_empty_topology() -> None:
    result = get_all_bestpractice_results(make_empty_topology())
    assert result["recommendations"] == []
    assert result["annotations"] == []
