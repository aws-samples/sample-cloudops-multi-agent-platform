"""Pytest port of source ``engine/__tests__/resiliency-rules.test.ts``.

Each Vitest ``it(...)`` maps 1:1 to a ``test_...`` function. Assertions preserve
the source's exact semantics (ghost-node counts, rule IDs, severity labels).
"""

from __future__ import annotations

from network_resilience.engine.resiliency_rules import (
    rule_no_lag,
    rule_no_tgw,
    rule_single_connection_per_location,
    rule_single_dx_location,
    rule_single_vgw,
)

from .helpers import conn, make_empty_topology


# ---------- rule_single_dx_location ----------


def test_single_dx_location_returns_none_when_empty() -> None:
    assert rule_single_dx_location(make_empty_topology()) is None


def test_single_dx_location_recommends_when_one_location() -> None:
    t = make_empty_topology()
    t["connections"] = [conn(connectionId="c1", location="EqDC2")]
    rec = rule_single_dx_location(t)
    assert rec is not None
    assert rec["ruleId"] == "single-dx-location"
    assert rec["severity"] == "info"
    assert len(rec["additionalNodes"]) > 0


def test_single_dx_location_returns_none_when_two_locations() -> None:
    t = make_empty_topology()
    t["connections"] = [
        conn(connectionId="c1", location="EqDC2"),
        conn(connectionId="c2", location="EqDC6"),
    ]
    assert rule_single_dx_location(t) is None


def test_single_dx_location_falls_back_to_vif_locations() -> None:
    t = make_empty_topology()
    t["virtualInterfaces"] = [
        {
            "virtualInterfaceId": "v1",
            "virtualInterfaceName": "vif-1",
            "location": "EqDC2",
            "bgpPeers": [],
        }
    ]
    rec = rule_single_dx_location(t)
    assert rec is not None
    assert rec["ruleId"] == "single-dx-location"


def test_single_dx_location_adds_edge_to_dxgw_when_present() -> None:
    t = make_empty_topology()
    t["connections"] = [conn(connectionId="c1", location="EqDC2")]
    t["dxGateways"] = [
        {"directConnectGatewayId": "gw-123", "directConnectGatewayName": "my-gw"}
    ]
    rec = rule_single_dx_location(t)
    assert rec is not None
    edges_to_gw = [
        e for e in rec["additionalEdges"] if e.get("target") == "dxgw-gw-123"
    ]
    assert edges_to_gw


# ---------- rule_single_dx_location target variants ----------


def test_single_dx_location_high_target_emits_five_nodes() -> None:
    t = make_empty_topology()
    t["connections"] = [conn(connectionId="c1", location="EqDC2")]
    rec = rule_single_dx_location(t, "high")
    assert rec is not None
    # customerSite + onPremise + dxLocation + partner + awsdev = 5
    assert len(rec["additionalNodes"]) == 5


def test_single_dx_location_maximum_target_emits_seven_nodes() -> None:
    t = make_empty_topology()
    t["connections"] = [conn(connectionId="c1", location="EqDC2")]
    rec = rule_single_dx_location(t, "maximum")
    assert rec is not None
    # Base 5 + second partner + second awsdev = 7
    assert len(rec["additionalNodes"]) == 7
    ids = {n["id"] for n in rec["additionalNodes"]}
    assert "rec-partner-B-2" in ids
    assert "rec-awsdev-B-2" in ids


# ---------- rule_single_connection_per_location ----------


def test_single_connection_per_location_high_target_returns_empty() -> None:
    t = make_empty_topology()
    t["connections"] = [conn(connectionId="c1", location="EqDC2")]
    assert rule_single_connection_per_location(t, "high") == []


def test_single_connection_per_location_empty_topology_returns_empty() -> None:
    assert (
        rule_single_connection_per_location(make_empty_topology(), "maximum")
        == []
    )


def test_single_connection_per_location_maximum_single_conn_recommends() -> None:
    t = make_empty_topology()
    t["connections"] = [conn(connectionId="c1", location="EqDC2")]
    t["locations"] = [
        {"locationCode": "EqDC2", "locationName": "Equinix DC2"}
    ]
    recs = rule_single_connection_per_location(t, "maximum")
    assert len(recs) == 1
    assert "Equinix DC2" in recs[0]["title"]


def test_single_connection_per_location_maximum_two_conns_same_device_recommends() -> None:
    """Two connections at one location sharing one device still fails the
    Max-tier device-redundancy check."""
    t = make_empty_topology()
    t["connections"] = [
        conn(
            connectionId="c1",
            location="EqDC2",
            awsLogicalDeviceId="dev-A",
        ),
        conn(
            connectionId="c2",
            location="EqDC2",
            awsLogicalDeviceId="dev-A",
        ),
    ]
    recs = rule_single_connection_per_location(t, "maximum")
    # Same device → 1 device, 2 connections → still needs redundancy rec
    assert len(recs) == 1
    assert "terminate on the same AWS logical device" in recs[0]["description"]


def test_single_connection_per_location_maximum_two_conns_two_devices_clean() -> None:
    t = make_empty_topology()
    t["connections"] = [
        conn(connectionId="c1", location="EqDC2", awsLogicalDeviceId="dev-A"),
        conn(connectionId="c2", location="EqDC2", awsLogicalDeviceId="dev-B"),
    ]
    assert rule_single_connection_per_location(t, "maximum") == []


def test_single_connection_per_location_maximum_one_rec_per_location() -> None:
    t = make_empty_topology()
    t["connections"] = [
        conn(connectionId="c1", location="EqDC2"),
        conn(connectionId="c2", location="EqDC6"),
    ]
    recs = rule_single_connection_per_location(t, "maximum")
    assert len(recs) == 2


# ---------- rule_no_tgw ----------


def test_no_tgw_returns_none_when_tgws_exist() -> None:
    t = make_empty_topology()
    t["transitGateways"] = [{"transitGatewayId": "tgw-1"}]
    assert rule_no_tgw(t) is None


def test_no_tgw_returns_none_when_no_vgws() -> None:
    assert rule_no_tgw(make_empty_topology()) is None


def test_no_tgw_recommends_when_vgws_but_no_tgws() -> None:
    t = make_empty_topology()
    t["vpnGateways"] = [{"vpnGatewayId": "vgw-1"}]
    rec = rule_no_tgw(t)
    assert rec is not None
    assert rec["ruleId"] == "no-tgw"
    assert rec["severity"] == "warning"


# ---------- rule_single_vgw ----------


def test_single_vgw_returns_none_when_no_vgws() -> None:
    assert rule_single_vgw(make_empty_topology()) is None


def test_single_vgw_recommends_when_one_vgw_and_no_tgw() -> None:
    t = make_empty_topology()
    t["vpnGateways"] = [{"vpnGatewayId": "vgw-1"}]
    rec = rule_single_vgw(t)
    assert rec is not None
    assert rec["ruleId"] == "single-vgw"


def test_single_vgw_returns_none_when_tgw_present() -> None:
    t = make_empty_topology()
    t["vpnGateways"] = [{"vpnGatewayId": "vgw-1"}]
    t["transitGateways"] = [{"transitGatewayId": "tgw-1"}]
    assert rule_single_vgw(t) is None


def test_single_vgw_returns_none_when_multiple_vgws() -> None:
    t = make_empty_topology()
    t["vpnGateways"] = [
        {"vpnGatewayId": "vgw-1"},
        {"vpnGatewayId": "vgw-2"},
    ]
    assert rule_single_vgw(t) is None


# ---------- rule_no_lag ----------


def test_no_lag_returns_none_when_lags_exist() -> None:
    t = make_empty_topology()
    t["lags"] = [{"lagId": "lag-1"}]
    assert rule_no_lag(t) is None


def test_no_lag_returns_none_when_fewer_than_two_connections() -> None:
    t = make_empty_topology()
    t["connections"] = [conn(connectionId="c1", location="EqDC2")]
    assert rule_no_lag(t) is None


def test_no_lag_recommends_when_two_plus_same_location_no_lags() -> None:
    t = make_empty_topology()
    t["connections"] = [
        conn(connectionId="c1", location="EqDC2"),
        conn(connectionId="c2", location="EqDC2"),
    ]
    rec = rule_no_lag(t)
    assert rec is not None
    assert rec["ruleId"] == "no-lag"
    assert rec["severity"] == "info"


def test_no_lag_returns_none_when_conns_at_different_locations() -> None:
    t = make_empty_topology()
    t["connections"] = [
        conn(connectionId="c1", location="EqDC2"),
        conn(connectionId="c2", location="EqDC6"),
    ]
    assert rule_no_lag(t) is None
