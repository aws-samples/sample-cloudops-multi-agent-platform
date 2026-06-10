"""EC2 API fetchers (VPCs, VPN, TGW, CGW).

Python port of source ``dx-visualizer/src/api/ec2.ts``.

boto3 returns PascalCase keys (``Vpcs``, ``VpcId``, ``AmazonSideAsn``); the JS
SDK returned camelCase. Output dicts use camelCase to match the source's
``TopologyData`` shape and the frontend renderer.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..types import (
    CustomerGateway,
    TgwRoute,
    TgwRouteTable,
    TgwRouteTableWithRoutes,
    TransitGateway,
    TransitGatewayAttachment,
    TransitGatewayPeeringAttachment,
    Vpc,
    VpcPeeringConnection,
    VpcRoute,
    VpcRouteTable,
    VpnConnection,
    VpnGateway,
)


def _tags_to_record(tags: List[Dict[str, str]] | None) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for t in tags or []:
        key = t.get("Key")
        if key:
            out[key] = t.get("Value", "") or ""
    return out


def fetch_vpcs(ec2_client: Any, region: str) -> List[Vpc]:
    res = ec2_client.describe_vpcs()
    return [
        {
            "vpcId": v.get("VpcId", ""),
            "cidrBlock": v.get("CidrBlock", ""),
            "tags": _tags_to_record(v.get("Tags")),
            "region": region,
            "state": v.get("State", ""),
        }
        for v in (res.get("Vpcs") or [])
    ]


def fetch_vpn_gateways(ec2_client: Any) -> List[VpnGateway]:
    res = ec2_client.describe_vpn_gateways(
        Filters=[{"Name": "state", "Values": ["available"]}]
    )
    return [
        {
            "vpnGatewayId": g.get("VpnGatewayId", ""),
            "vpcAttachments": [
                {"vpcId": a.get("VpcId", ""), "state": a.get("State", "")}
                for a in (g.get("VpcAttachments") or [])
            ],
            "type": g.get("Type", ""),
            "amazonSideAsn": int(g.get("AmazonSideAsn") or 0),
            "state": g.get("State", ""),
            "tags": _tags_to_record(g.get("Tags")),
        }
        for g in (res.get("VpnGateways") or [])
    ]


def fetch_transit_gateways(ec2_client: Any) -> List[TransitGateway]:
    res = ec2_client.describe_transit_gateways()
    out: List[TransitGateway] = []
    for t in res.get("TransitGateways") or []:
        opts = t.get("Options") or {}
        out.append(
            {
                "transitGatewayId": t.get("TransitGatewayId", ""),
                "transitGatewayArn": t.get("TransitGatewayArn", ""),
                "state": t.get("State", ""),
                "ownerId": t.get("OwnerId", ""),
                "description": t.get("Description", ""),
                "amazonSideAsn": int(opts.get("AmazonSideAsn") or 0),
                "tags": _tags_to_record(t.get("Tags")),
            }
        )
    return out


def fetch_transit_gateway_attachments(
    ec2_client: Any,
) -> List[TransitGatewayAttachment]:
    res = ec2_client.describe_transit_gateway_attachments()
    out: List[TransitGatewayAttachment] = []
    for a in res.get("TransitGatewayAttachments") or []:
        tags = _tags_to_record(a.get("Tags"))
        out.append(
            {
                "transitGatewayAttachmentId": a.get(
                    "TransitGatewayAttachmentId", ""
                ),
                "transitGatewayId": a.get("TransitGatewayId", ""),
                "resourceType": a.get("ResourceType", "vpc"),
                "resourceId": a.get("ResourceId", ""),
                "resourceOwnerId": a.get("ResourceOwnerId", ""),
                "state": a.get("State", ""),
                "name": tags.get("Name"),
            }
        )
    return out


_TGW_PEERING_SKIP_STATES = frozenset(
    {"deleted", "deleting", "failed", "rejected"}
)


def fetch_transit_gateway_peering_attachments(
    ec2_client: Any,
) -> List[TransitGatewayPeeringAttachment]:
    """Fetch TGW peering attachments in the caller's region.

    Filters out terminal / rejected states so the frontend only renders
    live peerings. Same state filter the TS side applies before mapping
    into the topology payload.
    """
    res = ec2_client.describe_transit_gateway_peering_attachments()
    out: List[TransitGatewayPeeringAttachment] = []
    for p in res.get("TransitGatewayPeeringAttachments") or []:
        state = p.get("State", "")
        if state in _TGW_PEERING_SKIP_STATES:
            continue
        requester = p.get("RequesterTgwInfo") or {}
        accepter = p.get("AccepterTgwInfo") or {}
        out.append(
            {
                "transitGatewayAttachmentId": p.get(
                    "TransitGatewayAttachmentId", ""
                ),
                "requesterTgwInfo": {
                    "transitGatewayId": requester.get(
                        "TransitGatewayId", ""
                    ),
                    "region": requester.get("Region", ""),
                    "ownerId": requester.get("OwnerId", ""),
                },
                "accepterTgwInfo": {
                    "transitGatewayId": accepter.get(
                        "TransitGatewayId", ""
                    ),
                    "region": accepter.get("Region", ""),
                    "ownerId": accepter.get("OwnerId", ""),
                },
                "state": state,
                "tags": _tags_to_record(p.get("Tags")),
            }
        )
    return out


_VPC_PEERING_SKIP_STATES = frozenset(
    {"deleted", "deleting", "failed", "rejected", "expired"}
)


def fetch_vpc_peering_connections(
    ec2_client: Any, region: str
) -> List[VpcPeeringConnection]:
    """Fetch VPC peering connections in the caller's region.

    Filters out terminal / rejected / expired states so the frontend only
    renders live peerings. The home-region for each side is filled from the
    boto3 response when AWS provides it; for in-region peerings AWS may omit
    the ``Region`` key on the requester/accepter, so we fall back to the
    caller region passed in.
    """
    res = ec2_client.describe_vpc_peering_connections()
    out: List[VpcPeeringConnection] = []
    for p in res.get("VpcPeeringConnections") or []:
        status = (p.get("Status") or {}).get("Code", "")
        if status in _VPC_PEERING_SKIP_STATES:
            continue
        requester = p.get("RequesterVpcInfo") or {}
        accepter = p.get("AccepterVpcInfo") or {}
        out.append(
            {
                "vpcPeeringConnectionId": p.get(
                    "VpcPeeringConnectionId", ""
                ),
                "state": status,
                "requesterVpc": {
                    "vpcId": requester.get("VpcId", ""),
                    "cidrBlock": requester.get("CidrBlock", ""),
                    "ownerId": requester.get("OwnerId", ""),
                    "region": requester.get("Region", "") or region,
                },
                "accepterVpc": {
                    "vpcId": accepter.get("VpcId", ""),
                    "cidrBlock": accepter.get("CidrBlock", ""),
                    "ownerId": accepter.get("OwnerId", ""),
                    "region": accepter.get("Region", "") or region,
                },
                "tags": _tags_to_record(p.get("Tags")),
            }
        )
    return out


def fetch_customer_gateways(ec2_client: Any) -> List[CustomerGateway]:
    res = ec2_client.describe_customer_gateways()
    return [
        {
            "customerGatewayId": c.get("CustomerGatewayId", ""),
            "bgpAsn": c.get("BgpAsn", ""),
            "ipAddress": c.get("IpAddress", ""),
            "state": c.get("State", ""),
            "type": c.get("Type", ""),
            "tags": _tags_to_record(c.get("Tags")),
        }
        for c in (res.get("CustomerGateways") or [])
    ]


def fetch_vpn_connections(ec2_client: Any) -> List[VpnConnection]:
    res = ec2_client.describe_vpn_connections()
    out: List[VpnConnection] = []
    for v in res.get("VpnConnections") or []:
        opts = v.get("Options") or {}
        tunnel_opts = opts.get("TunnelOptions") or []
        tunnel_opts_by_ip: Dict[str, Dict[str, Any]] = {}
        for o in tunnel_opts:
            ip = o.get("OutsideIpAddress")
            if not ip:
                continue
            tunnel_opts_by_ip[ip] = {
                "dpdTimeoutSeconds": o.get("DpdTimeoutSeconds"),
                "dpdTimeoutAction": o.get("DpdTimeoutAction"),
            }

        tunnels = []
        for t in v.get("VgwTelemetry") or []:
            ip = t.get("OutsideIpAddress", "") or ""
            extras = tunnel_opts_by_ip.get(ip, {})
            tunnel = {
                "outsideIpAddress": ip,
                "status": "UP" if t.get("Status") == "UP" else "DOWN",
                "statusMessage": t.get("StatusMessage"),
                "acceptedRouteCount": t.get("AcceptedRouteCount"),
                "dpdTimeoutSeconds": extras.get("dpdTimeoutSeconds"),
                "dpdTimeoutAction": extras.get("dpdTimeoutAction"),
            }
            tunnels.append(
                {k: v2 for k, v2 in tunnel.items() if v2 is not None}
            )

        out.append(
            {
                "vpnConnectionId": v.get("VpnConnectionId", ""),
                "vpnGatewayId": v.get("VpnGatewayId"),
                "transitGatewayId": v.get("TransitGatewayId"),
                "customerGatewayId": v.get("CustomerGatewayId", ""),
                "state": v.get("State", ""),
                "type": v.get("Type", ""),
                "category": v.get("Category", ""),
                "customerGatewayAddress": v.get(
                    "CustomerGatewayConfiguration", ""
                ),
                "tunnels": tunnels,
                "tags": _tags_to_record(v.get("Tags")),
            }
        )
    return out


def fetch_tgw_route_tables(
    ec2_client: Any, transit_gateway_id: str
) -> List[TgwRouteTable]:
    res = ec2_client.describe_transit_gateway_route_tables(
        Filters=[
            {"Name": "transit-gateway-id", "Values": [transit_gateway_id]}
        ]
    )
    return [
        {
            "transitGatewayRouteTableId": rt.get(
                "TransitGatewayRouteTableId", ""
            ),
            "transitGatewayId": rt.get("TransitGatewayId", ""),
            "state": rt.get("State", ""),
            "defaultAssociationRouteTable": rt.get(
                "DefaultAssociationRouteTable", False
            ),
            "defaultPropagationRouteTable": rt.get(
                "DefaultPropagationRouteTable", False
            ),
            "tags": _tags_to_record(rt.get("Tags")),
        }
        for rt in (res.get("TransitGatewayRouteTables") or [])
    ]


def fetch_tgw_routes(ec2_client: Any, route_table_id: str) -> List[TgwRoute]:
    res = ec2_client.search_transit_gateway_routes(
        TransitGatewayRouteTableId=route_table_id,
        Filters=[{"Name": "state", "Values": ["active", "blackhole"]}],
    )
    return [
        {
            "destinationCidrBlock": r.get("DestinationCidrBlock", ""),
            "transitGatewayAttachments": [
                {
                    "transitGatewayAttachmentId": a.get(
                        "TransitGatewayAttachmentId", ""
                    ),
                    "resourceType": a.get("ResourceType", ""),
                    "resourceId": a.get("ResourceId", ""),
                }
                for a in (r.get("TransitGatewayAttachments") or [])
            ],
            "type": "static" if r.get("Type") == "static" else "propagated",
            "state": "blackhole"
            if r.get("State") == "blackhole"
            else "active",
        }
        for r in (res.get("Routes") or [])
    ]


def fetch_tgw_route_tables_with_routes(
    ec2_client: Any, transit_gateway_id: str
) -> List[TgwRouteTableWithRoutes]:
    route_tables = fetch_tgw_route_tables(ec2_client, transit_gateway_id)
    return [
        {
            "routeTable": rt,
            "routes": fetch_tgw_routes(
                ec2_client, rt["transitGatewayRouteTableId"]
            ),
        }
        for rt in route_tables
    ]


def fetch_vpc_route_tables(ec2_client: Any) -> List[VpcRouteTable]:
    """Fetch every VPC route table in the caller's region.

    DescribeRouteTables returns all route tables in one paginated call —
    one trip handles all VPCs. The frontend keys the result by ``vpcId``
    so the VpcNode can render a Routes panel for each VPC.
    """
    out: List[VpcRouteTable] = []
    next_token: str | None = None
    while True:
        kwargs: Dict[str, Any] = {}
        if next_token:
            kwargs["NextToken"] = next_token
        res = ec2_client.describe_route_tables(**kwargs)
        for rt in res.get("RouteTables") or []:
            associations = rt.get("Associations") or []
            is_main = any(a.get("Main") is True for a in associations)
            associated_subnet_ids = [
                a["SubnetId"] for a in associations if a.get("SubnetId")
            ]
            routes: List[VpcRoute] = []
            for r in rt.get("Routes") or []:
                routes.append(
                    {
                        "destinationCidrBlock": r.get("DestinationCidrBlock", ""),
                        "destinationIpv6CidrBlock": r.get(
                            "DestinationIpv6CidrBlock", ""
                        ),
                        "destinationPrefixListId": r.get(
                            "DestinationPrefixListId", ""
                        ),
                        "gatewayId": r.get("GatewayId", ""),
                        "natGatewayId": r.get("NatGatewayId", ""),
                        "transitGatewayId": r.get("TransitGatewayId", ""),
                        "vpcPeeringConnectionId": r.get(
                            "VpcPeeringConnectionId", ""
                        ),
                        "networkInterfaceId": r.get("NetworkInterfaceId", ""),
                        "egressOnlyInternetGatewayId": r.get(
                            "EgressOnlyInternetGatewayId", ""
                        ),
                        "carrierGatewayId": r.get("CarrierGatewayId", ""),
                        "localGatewayId": r.get("LocalGatewayId", ""),
                        "coreNetworkArn": r.get("CoreNetworkArn", ""),
                        "instanceId": r.get("InstanceId", ""),
                        "origin": r.get("Origin", ""),
                        "state": "blackhole"
                        if r.get("State") == "blackhole"
                        else "active",
                    }
                )
            out.append(
                {
                    "routeTableId": rt.get("RouteTableId", ""),
                    "vpcId": rt.get("VpcId", ""),
                    "isMain": is_main,
                    "associatedSubnetIds": associated_subnet_ids,
                    "tags": _tags_to_record(rt.get("Tags")),
                    "routes": routes,
                }
            )
        next_token = res.get("NextToken")
        if not next_token:
            break
    return out
