"""Test fixtures — Python port of source ``engine/__tests__/helpers.ts``."""

from __future__ import annotations

from typing import Any, Dict


def make_empty_topology() -> Dict[str, Any]:
    """Return a valid empty TopologyData with every field present."""
    return {
        "connections": [],
        "virtualInterfaces": [],
        "dxGateways": [],
        "dxGatewayAssociations": [],
        "locations": [],
        "lags": [],
        "vpcs": [],
        "vpnGateways": [],
        "vpnConnections": [],
        "transitGateways": [],
        "transitGatewayAttachments": [],
        "transitGatewayPeeringAttachments": [],
        "customerGateways": [],
        "cloudWanCoreNetworks": [],
        "cloudWanAttachments": [],
        "cloudWanPeerings": [],
        "tgwRouteTables": {},
        "cloudWanRoutes": {},
    }


def conn(**overrides: Any) -> Dict[str, Any]:
    """Default connection with sensible fields; override specific keys per test."""
    base = {
        "connectionId": "c1",
        "connectionName": "conn-1",
        "location": "EqDC2",
        "connectionState": "available",
        "bandwidth": "1Gbps",
        "region": "us-east-1",
    }
    base.update(overrides)
    return base


def vif(**overrides: Any) -> Dict[str, Any]:
    base = {
        "virtualInterfaceId": "v1",
        "virtualInterfaceName": "vif-1",
        "virtualInterfaceType": "private",
        "virtualInterfaceState": "available",
        "connectionId": "c1",
        "vlan": 100,
        "asn": 65000,
        "bgpPeers": [],
        "region": "us-east-1",
    }
    base.update(overrides)
    return base
