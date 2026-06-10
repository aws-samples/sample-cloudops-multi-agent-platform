"""SLA gating — distinct-device counting per DX location.

Python port of source ``dx-visualizer/src/engine/sla-gating.ts`` (48 lines).

Max tier (99.99%) requires 2+ distinct AWS logical devices at every location.
Two connections terminating on the same ``awsLogicalDeviceId`` share a
physical device and don't survive a device failure.

This is the single source of truth for "how many redundant devices does
location X have?" — every consumer (tier determination, ghost-node rules,
scorecard, cost estimator) calls this rather than counting raw connections.
"""

from __future__ import annotations

from typing import Dict, Set

from ..types import TopologyData


def get_location_device_counts(topology: TopologyData) -> Dict[str, int]:
    """Returns ``{location_code: distinct_device_count}``.

    Prefers ``connection.awsLogicalDeviceId``; falls back to the VIF's when
    the connection doesn't carry one (hosted VIFs) or to the raw connection ID
    as a last resort — erring generous when AWS redacts device identity.

    When an account has no owned connections (pure hosted-VIF accounts), falls
    back to counting from VIFs directly.
    """
    location_devices: Dict[str, Set[str]] = {}

    def add_device(loc: str, device_key: str) -> None:
        if not loc:
            return
        location_devices.setdefault(loc, set()).add(device_key)

    connections = topology.get("connections") or []
    vifs = topology.get("virtualInterfaces") or []

    if connections:
        # Build a lookup: conn.connectionId -> vif for fast fallback
        vif_by_conn: Dict[str, Dict] = {}
        for v in vifs:
            cid = v.get("connectionId")
            if cid and cid not in vif_by_conn:
                vif_by_conn[cid] = v

        for conn in connections:
            vif = vif_by_conn.get(conn.get("connectionId", ""), {})
            device_key = (
                conn.get("awsLogicalDeviceId")
                or vif.get("awsLogicalDeviceId")
                or conn.get("connectionId", "")
            )
            add_device(conn.get("location", ""), device_key)
    else:
        # Fallback: no owned connections — infer from VIFs.
        for vif in vifs:
            device_key = (
                vif.get("awsLogicalDeviceId")
                or vif.get("connectionId")
                or vif.get("virtualInterfaceId", "")
            )
            add_device(vif.get("location") or "", device_key)

    return {loc: len(devices) for loc, devices in location_devices.items()}
