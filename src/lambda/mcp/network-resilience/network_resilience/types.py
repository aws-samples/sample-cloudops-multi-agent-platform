"""Python equivalents of the original network-resiliency visualizer's
``types/aws-resources.ts`` and ``types/topology.ts``.

Field names are preserved in camelCase to match the TS side so JSON payloads
flow unchanged between the Lambda output and the frontend's React Flow
renderer. Optional fields use ``total=False`` TypedDict segments; required
fields are in the base class.

Frontend-only types (``DxNode``, ``DxEdge``, ``GraphData``, ``NodeCategory``)
are NOT ported here — they live in the frontend package under
``src/frontend/src/lib/topology/``.
"""

from __future__ import annotations

from typing import Dict, List, Literal, TypedDict


# ----- Direct Connect -------------------------------------------------------


class DxConnection(TypedDict, total=False):
    connectionId: str
    connectionName: str
    connectionState: str
    location: str
    bandwidth: str
    lagId: str
    partnerName: str
    vlan: int
    region: str
    hasBfd: bool
    awsDeviceV2: str
    awsLogicalDeviceId: str
    # True when synthesized from a VIF whose physical connection is owned by
    # another account (hosted VIF path). Visualizer renders these with an
    # amber accent in Phase 5.
    isInferred: bool


class BgpPeer(TypedDict, total=False):
    bgpPeerId: str
    bgpPeerState: str
    bgpStatus: str
    asn: int
    customerAddress: str
    amazonAddress: str


VirtualInterfaceType = Literal["private", "public", "transit"]


class DxVirtualInterface(TypedDict, total=False):
    virtualInterfaceId: str
    virtualInterfaceName: str
    virtualInterfaceType: VirtualInterfaceType
    virtualInterfaceState: str
    connectionId: str
    directConnectGatewayId: str
    virtualGatewayId: str
    vlan: int
    asn: int
    bgpPeers: List[BgpPeer]
    region: str
    location: str
    ownerAccount: str
    awsDeviceV2: str
    awsLogicalDeviceId: str


class DxGateway(TypedDict, total=False):
    directConnectGatewayId: str
    directConnectGatewayName: str
    amazonSideAsn: int
    directConnectGatewayState: str


AssociatedGatewayType = Literal["virtualPrivateGateway", "transitGateway"]


class AssociatedGateway(TypedDict, total=False):
    id: str
    type: AssociatedGatewayType
    region: str
    ownerAccount: str


class AssociatedCoreNetwork(TypedDict, total=False):
    id: str
    ownerAccount: str
    attachmentId: str


class DxGatewayAssociation(TypedDict, total=False):
    directConnectGatewayId: str
    associationId: str
    associatedGateway: AssociatedGateway
    # Populated when the DXGW is associated directly to a Cloud WAN core
    # network (AWS returns this in ``associatedCoreNetwork`` instead of
    # ``associatedGateway``). The frontend draws a DXGW → Core Network edge
    # for these instead of treating them as stub VGW/TGW associations.
    associatedCoreNetwork: AssociatedCoreNetwork
    associationState: str
    allowedPrefixes: List[str]
    # True when AWS returned a stub association (no gateway id/type) AND the
    # proposals backfill couldn't resolve it. Surfaced in the Hidden
    # Associations zone.
    isPrefixPoolStub: bool


class DxLocation(TypedDict, total=False):
    locationCode: str
    locationName: str
    region: str
    availablePortSpeeds: List[str]


class DxLag(TypedDict, total=False):
    lagId: str
    lagName: str
    connectionsBandwidth: str
    numberOfConnections: int
    location: str
    lagState: str
    connections: List[DxConnection]


# ----- EC2 / VPC ------------------------------------------------------------


class Vpc(TypedDict, total=False):
    vpcId: str
    cidrBlock: str
    tags: Dict[str, str]
    region: str
    state: str
    ownerAccountId: str


class VpcAttachment(TypedDict, total=False):
    vpcId: str
    state: str


class VpnGateway(TypedDict, total=False):
    vpnGatewayId: str
    vpcAttachments: List[VpcAttachment]
    type: str
    amazonSideAsn: int
    state: str
    tags: Dict[str, str]


class TransitGateway(TypedDict, total=False):
    transitGatewayId: str
    transitGatewayArn: str
    state: str
    ownerId: str
    description: str
    amazonSideAsn: int
    tags: Dict[str, str]


TgwAttachmentResourceType = Literal[
    "vpc", "vpn", "direct-connect-gateway", "peering", "connect"
]


class TransitGatewayAttachment(TypedDict, total=False):
    transitGatewayAttachmentId: str
    transitGatewayId: str
    resourceType: TgwAttachmentResourceType
    resourceId: str
    resourceOwnerId: str
    state: str
    # Tag-derived display name, populated for categories that render as
    # standalone nodes (e.g. connect).
    name: str


class TgwPeeringInfo(TypedDict, total=False):
    transitGatewayId: str
    region: str
    ownerId: str


class TransitGatewayPeeringAttachment(TypedDict, total=False):
    transitGatewayAttachmentId: str
    requesterTgwInfo: TgwPeeringInfo
    accepterTgwInfo: TgwPeeringInfo
    state: str
    tags: Dict[str, str]


class VpcPeeringEndpoint(TypedDict, total=False):
    vpcId: str
    cidrBlock: str
    ownerId: str
    region: str


class VpcPeeringConnection(TypedDict, total=False):
    vpcPeeringConnectionId: str
    state: str
    requesterVpc: VpcPeeringEndpoint
    accepterVpc: VpcPeeringEndpoint
    tags: Dict[str, str]


class VpnTunnel(TypedDict, total=False):
    outsideIpAddress: str
    status: Literal["UP", "DOWN"]
    statusMessage: str
    acceptedRouteCount: int
    # AWS-side DPD only. Customer-gateway-side DPD is not exposed by any AWS
    # API.
    dpdTimeoutSeconds: int
    dpdTimeoutAction: str


class VpnConnection(TypedDict, total=False):
    vpnConnectionId: str
    vpnGatewayId: str
    transitGatewayId: str
    customerGatewayId: str
    state: str
    type: str
    category: str
    customerGatewayAddress: str
    tunnels: List[VpnTunnel]
    tags: Dict[str, str]


class CustomerGateway(TypedDict, total=False):
    customerGatewayId: str
    bgpAsn: str
    ipAddress: str
    state: str
    type: str
    tags: Dict[str, str]


# ----- Cloud WAN ------------------------------------------------------------


class CloudWanEdge(TypedDict, total=False):
    edgeLocation: str
    asn: int
    insideCidrBlocks: List[str]


class CloudWanSegment(TypedDict, total=False):
    name: str
    edgeLocations: List[str]
    sharedSegments: List[str]


class CloudWanCoreNetwork(TypedDict, total=False):
    coreNetworkId: str
    coreNetworkArn: str
    globalNetworkId: str
    description: str
    state: str
    edges: List[CloudWanEdge]
    segments: List[CloudWanSegment]


CloudWanAttachmentType = Literal[
    "vpc",
    "site-to-site-vpn",
    "transit-gateway-route-table",
    "connect",
    "direct-connect-gateway",
]


class CloudWanAttachment(TypedDict, total=False):
    attachmentId: str
    coreNetworkId: str
    ownerAccountId: str
    attachmentType: CloudWanAttachmentType
    edgeLocation: str
    resourceArn: str
    segmentName: str
    state: str
    tags: Dict[str, str]


class CloudWanPeering(TypedDict, total=False):
    peeringId: str
    coreNetworkId: str
    peeringType: str
    edgeLocation: str
    resourceArn: str
    state: str
    tags: Dict[str, str]


class CloudWanRouteDestination(TypedDict, total=False):
    coreNetworkAttachmentId: str
    segmentName: str
    edgeLocation: str
    resourceType: str
    resourceId: str


RouteType = Literal["static", "propagated"]
RouteState = Literal["active", "blackhole"]


class CloudWanRoute(TypedDict, total=False):
    destinationCidrBlock: str
    destinations: List[CloudWanRouteDestination]
    type: RouteType
    state: RouteState


class CloudWanSegmentRoutes(TypedDict, total=False):
    segmentName: str
    edgeLocation: str
    routes: List[CloudWanRoute]


# ----- TGW route tables -----------------------------------------------------


class TgwRouteTable(TypedDict, total=False):
    transitGatewayRouteTableId: str
    transitGatewayId: str
    state: str
    defaultAssociationRouteTable: bool
    defaultPropagationRouteTable: bool
    tags: Dict[str, str]


class TgwRouteAttachment(TypedDict, total=False):
    transitGatewayAttachmentId: str
    resourceType: str
    resourceId: str


class TgwRoute(TypedDict, total=False):
    destinationCidrBlock: str
    transitGatewayAttachments: List[TgwRouteAttachment]
    type: RouteType
    state: RouteState


class TgwRouteTableWithRoutes(TypedDict, total=False):
    routeTable: TgwRouteTable
    routes: List[TgwRoute]


# ----- VPC route tables -----------------------------------------------------


class VpcRoute(TypedDict, total=False):
    destinationCidrBlock: str
    destinationIpv6CidrBlock: str
    destinationPrefixListId: str
    gatewayId: str
    natGatewayId: str
    transitGatewayId: str
    vpcPeeringConnectionId: str
    networkInterfaceId: str
    egressOnlyInternetGatewayId: str
    carrierGatewayId: str
    localGatewayId: str
    coreNetworkArn: str
    instanceId: str
    origin: str
    state: RouteState


class VpcRouteTable(TypedDict, total=False):
    routeTableId: str
    vpcId: str
    isMain: bool
    associatedSubnetIds: List[str]
    tags: Dict[str, str]
    routes: List[VpcRoute]


# ----- Health events --------------------------------------------------------


class DxMaintenanceEvent(TypedDict, total=False):
    arn: str
    eventTypeCode: str
    region: str
    startTime: str
    endTime: str
    lastUpdatedTime: str
    statusCode: str
    affectedResourceIds: List[str]
    description: str
    accountId: str


# ----- CloudWatch BGP prefix metrics ----------------------------------------


class BgpPrefixMetric(TypedDict, total=False):
    accepted: int
    advertised: int


class VifUtilization(TypedDict, total=False):
    """Peak hourly bitrate per VIF over a configured window (30/60/90 days).

    AWS-side perspective: ``ingressBpsPeak`` is data INTO AWS (customer →
    AWS), ``egressBpsPeak`` is data OUT of AWS. Names match the AWS/DX
    metric convention (``VirtualInterfaceBpsIngress`` / ``...Egress``).
    """

    ingressBpsPeak: int
    egressBpsPeak: int


class ConnectionUtilization(TypedDict, total=False):
    """Peak hourly bitrate aggregated across every VIF on a DX Connection.

    AWS does not publish a port-level bps metric. We sum across sibling
    VIFs hour-by-hour, then take the worst hour. Misses LACP/BFD overhead,
    so it's a slight underestimate of true port utilization — same caveat
    the AWS console shows under a Connection's Monitoring tab.
    """

    ingressBpsPeak: int
    egressBpsPeak: int


UtilizationWindowDays = Literal[30, 60, 90]


# ----- Aggregate topology result --------------------------------------------


class TopologyData(TypedDict, total=False):
    """Output of ``topology.fetch.fetch_all_topology_data``.

    Maps maintain object identity: ``tgwRouteTables`` is keyed by TGW ID,
    ``cloudWanRoutes`` by core network ID, ``bgpPrefixMetrics`` by VIF ID,
    ``regionNames`` by region string. Serialized to JSON as plain objects.
    """

    connections: List[DxConnection]
    virtualInterfaces: List[DxVirtualInterface]
    dxGateways: List[DxGateway]
    dxGatewayAssociations: List[DxGatewayAssociation]
    locations: List[DxLocation]
    lags: List[DxLag]
    vpcs: List[Vpc]
    vpnGateways: List[VpnGateway]
    vpnConnections: List[VpnConnection]
    transitGateways: List[TransitGateway]
    transitGatewayAttachments: List[TransitGatewayAttachment]
    transitGatewayPeeringAttachments: List[TransitGatewayPeeringAttachment]
    vpcPeerings: List[VpcPeeringConnection]
    customerGateways: List[CustomerGateway]
    cloudWanCoreNetworks: List[CloudWanCoreNetwork]
    cloudWanAttachments: List[CloudWanAttachment]
    cloudWanPeerings: List[CloudWanPeering]
    tgwRouteTables: Dict[str, List[TgwRouteTableWithRoutes]]
    vpcRouteTables: Dict[str, List[VpcRouteTable]]
    cloudWanRoutes: Dict[str, List[CloudWanSegmentRoutes]]
    bgpPrefixMetrics: Dict[str, BgpPrefixMetric]
    maintenanceEvents: List[DxMaintenanceEvent]
    homeAccountId: str
    regionNames: Dict[str, str]
    # Metadata surfaced to the agent + frontend when discovery partially
    # failed (e.g., some regional DescribeVpcs returned AccessDenied).
    # Contains one string per failed sub-call, safe to expose to humans.
    fetchErrors: List[str]
    # Name of the mock scenario this topology was synthesised from (e.g.
    # "cloudWan", "crossAccount"). Absent when the topology was fetched from
    # live AWS. Frontend surfaces this to the user and report generation uses
    # it to caveat that numbers are demo data, not the live environment.
    mockScenario: str


# ----- Mock scenario names (source utils/mock-data.ts) ----------------------


MockScenarioName = Literal[
    "noResiliency",
    "devTest",
    "high",
    "maximum",
    "crossAccount",
    "cloudWan",
]


# ----- Recommendations / Assessment (source types/recommendations.ts) ------


ResiliencyLevel = Literal["none", "devtest", "high", "maximum"]
"""Tier names for resiliency:
- none: no DX at all
- devtest: single connection SLA (95%)
- high: 2+ locations (99.9% SLA)
- maximum: 2+ locations AND 2+ distinct AWS logical devices per location (99.99%)
"""

ResiliencyTarget = Literal["high", "maximum"]

Severity = Literal["critical", "warning", "info"]

RecommendationCategory = Literal["resiliency", "bestpractice"]

BadgeType = Literal["warning", "info", "error"]


class GhostNodeSpec(TypedDict, total=False):
    """A "recommended infrastructure" node emitted by a rule.

    The frontend (Phase 5) hydrates these into React Flow nodes with
    positioning via the layout engine. Python only emits identity + category
    + label + optional details.

    Ghost node IDs follow strict prefixes the frontend keys off:
        rec-{dxgwId}-{kind}-{suffix}   (DXGW-scoped recommendations)
        rec-{kind}-{suffix}            (topology-wide recommendations)
    """

    id: str
    category: str  # customerSite, onPremise, dxPartnerDevice, awsDevice, ...
    label: str
    isRecommended: bool  # always True for ghosts
    details: Dict[str, str]


class GhostEdgeSpec(TypedDict, total=False):
    id: str
    source: str
    target: str
    label: str
    labelPosition: float
    isRecommended: bool


class NodeBadge(TypedDict, total=False):
    type: BadgeType
    label: str
    description: str


class NodeAnnotation(TypedDict, total=False):
    nodeId: str
    badge: NodeBadge


class Recommendation(TypedDict, total=False):
    id: str
    ruleId: str
    category: RecommendationCategory
    severity: Severity
    title: str
    description: str
    additionalNodes: List[GhostNodeSpec]
    additionalEdges: List[GhostEdgeSpec]


class ResiliencyAssessment(TypedDict, total=False):
    currentLevel: ResiliencyLevel
    targetLevel: ResiliencyLevel
    score: int
    recommendations: List[Recommendation]


class BestPracticeAssessment(TypedDict, total=False):
    annotations: List[NodeAnnotation]
    recommendations: List[Recommendation]


class DxGatewayAssessment(TypedDict, total=False):
    dxGatewayId: str
    dxGatewayName: str
    currentLevel: ResiliencyLevel
    targetLevel: ResiliencyLevel
    score: int
    locationCount: int
    connectionCount: int
    # True when the DXGW has no VIFs — resiliency tiering does not apply.
    # The frontend uses this to replace the tier chip with an "Unattached"
    # badge and hide the upgrade-path / protection-coverage sections.
    isUnattached: bool
    # True when at least one Virtual Interface points at this DXGW.
    hasVif: bool
    # True when the DXGW has at least one TGW/VGW association.
    hasAssociation: bool
    # Recommendations scoped to this DX Gateway (resiliency + best-practice).
    recommendations: List[Recommendation]


class GlobalAssessment(TypedDict, total=False):
    resiliency: ResiliencyAssessment
    bestPractice: BestPracticeAssessment


class CombinedAssessment(TypedDict, total=False):
    # Per-DXGW resilience cards. Empty when topology has no DX Gateways.
    perDxGateway: List[DxGatewayAssessment]
    # Topology-wide rules that don't pin to a specific DX Gateway.
    # Source field is "global" — reserved keyword in Python, so we expose it
    # as a dict key via TypedDict (field name is "global"). Python callers
    # use ``combined["global"]`` to access.
    # (TypedDict field names CAN be Python keywords; access via subscript.)
    # The JSON output to the agent uses the source field name "global".
    # Aggregated view of all resiliency recommendations (per-DXGW + global).
    resiliency: ResiliencyAssessment
    # Aggregated view of all best-practice recommendations.
    bestPractice: BestPracticeAssessment


# The "global" field on CombinedAssessment — TypedDict classes can't have
# reserved keyword fields via class body syntax. Use the functional form.
CombinedAssessment.__annotations__["global"] = GlobalAssessment
