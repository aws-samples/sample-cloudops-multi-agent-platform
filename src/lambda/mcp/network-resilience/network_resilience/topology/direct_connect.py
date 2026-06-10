"""Direct Connect API fetchers.

Python port of source ``dx-visualizer/src/api/direct-connect.ts``. Preserves
field names (camelCase output matching source) and semantics, especially the
DXGW association stub backfill via proposals — cross-account EDGLESS-origin
associations come back redacted when viewed from the DXGW owner account.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..types import (
    DxConnection,
    DxGateway,
    DxGatewayAssociation,
    DxLag,
    DxLocation,
    DxVirtualInterface,
)

logger = logging.getLogger(__name__)


def fetch_connections(dx_client: Any) -> List[DxConnection]:
    res = dx_client.describe_connections()
    out: List[DxConnection] = []
    for c in res.get("connections", []) or []:
        out.append(
            {
                "connectionId": c.get("connectionId", ""),
                "connectionName": c.get("connectionName", ""),
                "connectionState": c.get("connectionState", ""),
                "location": c.get("location", ""),
                "bandwidth": c.get("bandwidth", ""),
                "region": c.get("region", ""),
                "lagId": c.get("lagId"),
                "partnerName": c.get("partnerName"),
                "vlan": c.get("vlan"),
                "hasBfd": False,
                "awsDeviceV2": c.get("awsDeviceV2"),
                "awsLogicalDeviceId": c.get("awsLogicalDeviceId"),
            }
        )
    return [_drop_none(x) for x in out]


def fetch_virtual_interfaces(dx_client: Any) -> List[DxVirtualInterface]:
    res = dx_client.describe_virtual_interfaces()
    out: List[DxVirtualInterface] = []
    for v in res.get("virtualInterfaces", []) or []:
        out.append(
            {
                "virtualInterfaceId": v.get("virtualInterfaceId", ""),
                "virtualInterfaceName": v.get("virtualInterfaceName", ""),
                "virtualInterfaceType": v.get("virtualInterfaceType", "private"),
                "virtualInterfaceState": v.get("virtualInterfaceState", ""),
                "connectionId": v.get("connectionId", ""),
                "directConnectGatewayId": v.get("directConnectGatewayId"),
                "virtualGatewayId": v.get("virtualGatewayId"),
                "vlan": v.get("vlan", 0),
                "asn": v.get("asn", 0),
                "bgpPeers": [
                    {
                        "bgpPeerId": p.get("bgpPeerId", ""),
                        "bgpPeerState": p.get("bgpPeerState", ""),
                        "bgpStatus": p.get("bgpStatus", ""),
                        "asn": p.get("asn", 0),
                        "customerAddress": p.get("customerAddress", ""),
                        "amazonAddress": p.get("amazonAddress", ""),
                    }
                    for p in (v.get("bgpPeers") or [])
                ],
                "region": v.get("region", ""),
                "location": v.get("location"),
                "ownerAccount": v.get("ownerAccount"),
                "awsDeviceV2": v.get("awsDeviceV2"),
                "awsLogicalDeviceId": v.get("awsLogicalDeviceId"),
            }
        )
    return [_drop_none(x) for x in out]


def fetch_dx_gateways(dx_client: Any) -> List[DxGateway]:
    """Paginates via ``nextToken``."""
    out: List[DxGateway] = []
    next_token: str | None = None
    while True:
        kwargs: Dict[str, Any] = {}
        if next_token:
            kwargs["nextToken"] = next_token
        res = dx_client.describe_direct_connect_gateways(**kwargs)
        for g in res.get("directConnectGateways", []) or []:
            out.append(
                {
                    "directConnectGatewayId": g.get("directConnectGatewayId", ""),
                    "directConnectGatewayName": g.get(
                        "directConnectGatewayName", ""
                    ),
                    "amazonSideAsn": int(g.get("amazonSideAsn") or 0),
                    "directConnectGatewayState": g.get(
                        "directConnectGatewayState", ""
                    ),
                }
            )
        next_token = res.get("nextToken")
        if not next_token:
            break
    return out


def _fetch_proposal_backfills(
    dx_client: Any, gateway_id: str
) -> List[Dict[str, Any]]:
    """For DXGW associations that come back as stubs (no id/type), AWS redacts
    identity on the DXGW-owner view for cross-account EDGLESS associations.
    Proposals retain the associated gateway identity, so use them as backfill.
    Mirrors source ``fetchProposalBackfills``.
    """
    out: List[Dict[str, Any]] = []
    next_token: str | None = None
    while True:
        kwargs: Dict[str, Any] = {"directConnectGatewayId": gateway_id}
        if next_token:
            kwargs["nextToken"] = next_token
        res = dx_client.describe_direct_connect_gateway_association_proposals(
            **kwargs
        )
        for p in (
            res.get("directConnectGatewayAssociationProposals", []) or []
        ):
            if p.get("proposalState") != "accepted":
                continue
            g = p.get("associatedGateway") or {}
            if not g.get("id"):
                continue
            allowed = (
                p.get("requestedAllowedPrefixesToDirectConnectGateway")
                or p.get("existingAllowedPrefixesToDirectConnectGateway")
                or []
            )
            out.append(
                {
                    "id": g.get("id"),
                    "type": g.get("type"),
                    "region": g.get("region", ""),
                    "ownerAccount": g.get("ownerAccount", ""),
                    "allowedPrefixes": [
                        r.get("cidr", "")
                        for r in allowed
                        if r.get("cidr")
                    ],
                }
            )
        next_token = res.get("nextToken")
        if not next_token:
            break
    return out


def fetch_dx_gateway_associations(
    dx_client: Any, gateway_id: str
) -> List[DxGatewayAssociation]:
    """Fetch all associations for a single DXGW with proposal backfill for
    stub records.
    """
    mapped: List[DxGatewayAssociation] = []
    stub_indices: List[int] = []
    next_token: str | None = None
    pages = 0
    while True:
        kwargs: Dict[str, Any] = {"directConnectGatewayId": gateway_id}
        if next_token:
            kwargs["nextToken"] = next_token
        res = dx_client.describe_direct_connect_gateway_associations(**kwargs)
        raw = res.get("directConnectGatewayAssociations", []) or []
        for a in raw:
            ag = a.get("associatedGateway") or {}
            acn = a.get("associatedCoreNetwork") or {}
            has_core_network = bool(acn.get("id"))
            # Cloud WAN associations populate ``associatedCoreNetwork``
            # instead of ``associatedGateway``, so a missing gateway id
            # there is expected — don't treat them as stubs to backfill
            # from proposals.
            is_stub = (not has_core_network) and (
                not ag.get("id") or not ag.get("type")
            )
            if is_stub:
                stub_indices.append(len(mapped))
            entry: Dict[str, Any] = {
                "directConnectGatewayId": a.get(
                    "directConnectGatewayId", ""
                ),
                "associationId": a.get("associationId"),
                "associatedGateway": {
                    "id": ag.get("id", ""),
                    "type": ag.get("type"),
                    "region": ag.get("region", ""),
                    "ownerAccount": ag.get("ownerAccount", ""),
                },
                "associationState": a.get("associationState", ""),
                "allowedPrefixes": [
                    p.get("cidr", "")
                    for p in (
                        a.get("allowedPrefixesToDirectConnectGateway")
                        or []
                    )
                    if p.get("cidr")
                ],
            }
            if has_core_network:
                entry["associatedCoreNetwork"] = {
                    "id": acn.get("id", ""),
                    "ownerAccount": acn.get("ownerAccount", ""),
                    "attachmentId": acn.get("attachmentId", ""),
                }
            mapped.append(entry)
        next_token = res.get("nextToken")
        pages += 1
        if not next_token:
            break

    if pages > 1:
        logger.info(
            "[dx] DxGwAssoc(%s) paginated: %d pages, %d total",
            gateway_id,
            pages,
            len(mapped),
        )

    if stub_indices:
        try:
            backfills = _fetch_proposal_backfills(dx_client, gateway_id)
        except Exception as err:  # noqa: BLE001
            logger.warning(
                "[dx] proposal backfill failed for %s: %s", gateway_id, err
            )
            backfills = []
        claimed: set[int] = set()
        for b in backfills:
            # Stubs carry no identifying info; match 1:1 in arrival order.
            slot = next((i for i in stub_indices if i not in claimed), None)
            if slot is None:
                break
            claimed.add(slot)
            prev = mapped[slot]
            mapped[slot] = {
                **prev,
                "associatedGateway": {
                    "id": b["id"],
                    "type": b["type"],
                    "region": b["region"],
                    "ownerAccount": b["ownerAccount"],
                },
                "allowedPrefixes": (
                    b["allowedPrefixes"]
                    if b["allowedPrefixes"]
                    else prev.get("allowedPrefixes", [])
                ),
            }
        remaining = [i for i in stub_indices if i not in claimed]
        if claimed:
            logger.info(
                "[dx] DxGwAssoc(%s): backfilled %d/%d stub(s) from proposals",
                gateway_id,
                len(claimed),
                len(stub_indices),
            )
        for i in remaining:
            mapped[i]["isPrefixPoolStub"] = True
            logger.warning(
                "[dx] incomplete DX gateway association (no matching proposal): "
                "dxgw=%s state=%s",
                mapped[i].get("directConnectGatewayId"),
                mapped[i].get("associationState"),
            )

    return mapped


def fetch_locations(dx_client: Any) -> List[DxLocation]:
    res = dx_client.describe_locations()
    return [
        {
            "locationCode": l.get("locationCode", ""),
            "locationName": l.get("locationName", ""),
            "region": l.get("region", ""),
            "availablePortSpeeds": l.get("availablePortSpeeds") or [],
        }
        for l in (res.get("locations") or [])
    ]


def fetch_lags(dx_client: Any) -> List[DxLag]:
    res = dx_client.describe_lags()
    out: List[DxLag] = []
    for l in res.get("lags", []) or []:
        out.append(
            {
                "lagId": l.get("lagId", ""),
                "lagName": l.get("lagName", ""),
                "connectionsBandwidth": l.get("connectionsBandwidth", ""),
                "numberOfConnections": l.get("numberOfConnections", 0),
                "location": l.get("location", ""),
                "lagState": l.get("lagState", ""),
                "connections": [
                    _drop_none(
                        {
                            "connectionId": c.get("connectionId", ""),
                            "connectionName": c.get("connectionName", ""),
                            "connectionState": c.get("connectionState", ""),
                            "location": c.get("location", ""),
                            "bandwidth": c.get("bandwidth", ""),
                            "region": c.get("region", ""),
                            "lagId": c.get("lagId"),
                            "partnerName": c.get("partnerName"),
                            "vlan": c.get("vlan"),
                        }
                    )
                    for c in (l.get("connections") or [])
                ],
            }
        )
    return out


def _drop_none(d: Dict[str, Any]) -> Dict[str, Any]:
    """Omit keys whose value is None so JSON stays tidy. The source TS
    serialization preserves ``undefined`` as missing keys; we mirror that.
    """
    return {k: v for k, v in d.items() if v is not None}
