import type { Node, Edge } from '@xyflow/react';
import type {
  DxConnection,
  DxVirtualInterface,
  DxGateway,
  DxGatewayAssociation,
  DxLocation,
  DxLag,
  Vpc,
  VpnGateway,
  VpnConnection,
  VpnTunnel,
  TransitGateway,
  TransitGatewayAttachment,
  TransitGatewayPeeringAttachment,
  VpcPeeringConnection,
  CustomerGateway,
  CloudWanCoreNetwork,
  CloudWanAttachment,
  CloudWanPeering,
  TgwRouteTableWithRoutes,
  CloudWanSegmentRoutes,
  DxMaintenanceEvent,
  VpcRouteTable,
} from './aws-resources';

export interface TopologyData {
  connections: DxConnection[];
  virtualInterfaces: DxVirtualInterface[];
  dxGateways: DxGateway[];
  dxGatewayAssociations: DxGatewayAssociation[];
  locations: DxLocation[];
  lags: DxLag[];
  vpcs: Vpc[];
  vpnGateways: VpnGateway[];
  vpnConnections: VpnConnection[];
  transitGateways: TransitGateway[];
  transitGatewayAttachments: TransitGatewayAttachment[];
  transitGatewayPeeringAttachments: TransitGatewayPeeringAttachment[];
  vpcPeerings?: VpcPeeringConnection[];
  customerGateways: CustomerGateway[];
  cloudWanCoreNetworks: CloudWanCoreNetwork[];
  cloudWanAttachments: CloudWanAttachment[];
  cloudWanPeerings: CloudWanPeering[];
  tgwRouteTables: Map<string, TgwRouteTableWithRoutes[]>;
  cloudWanRoutes: Map<string, CloudWanSegmentRoutes[]>;
  vpcRouteTables: Map<string, VpcRouteTable[]>;
  bgpPrefixMetrics?: Map<string, { accepted?: number; advertised?: number }>;
  // Peak hourly bps per VIF over the user-selected window (30/60/90 days).
  // Populated only when the user enables "Show utilization" in the toolbar.
  vifUtilization?: Map<string, { ingressBpsPeak?: number; egressBpsPeak?: number }>;
  // Peak hourly bps per DX Connection (sum of sibling VIFs hour-by-hour).
  connectionUtilization?: Map<string, { ingressBpsPeak?: number; egressBpsPeak?: number }>;
  // Window currently materialized in *Utilization fields above. Lets the
  // frontend cache results per-window (toggling 30↔60↔90 within a session
  // reuses the prior fetch instead of re-billing CloudWatch).
  utilizationWindowDays?: 30 | 60 | 90;
  maintenanceEvents?: DxMaintenanceEvent[];
  homeAccountId?: string;
  regionNames?: Map<string, string>;
}

export type ViewMode = 'current' | 'recommended';

export interface GraphData {
  nodes: Node[];
  edges: Edge[];
}

export type NodeCategory =
  | 'customerSite'
  | 'onPremise'
  | 'cgw'
  | 'dxLocation'
  | 'dxPartnerDevice'
  | 'dxPartnerDeviceGroup'
  | 'awsDevice'
  | 'dxGateway'
  | 'tgw'
  | 'tgwConnect'
  | 'vgw'
  | 'vpc'
  | 'vpcGroup'
  | 'tgwGroup'
  | 'isolatedTgwGroup'
  | 'region'
  | 'unattachedZone'
  | 'hiddenAssocZone'
  | 'awsCloud'
  | 'coreNetwork';

export interface VpcChildInfo {
  vpcId: string;
  name: string;
  cidr: string;
  state: string;
  region?: string;
  crossAccount?: boolean;
  ownerAccount?: string;
}

export interface TgwChildInfo {
  tgwId: string;
  name: string;
  state: string;
  asn?: string;
  region?: string;
  crossAccount?: boolean;
  ownerAccount?: string;
}

export interface VgwChildInfo {
  vgwId: string;
  name: string;
  state: string;
  asn?: string;
  region?: string;
  attachmentState?: string;
}

export interface DxgwChildInfo {
  dxgwId: string;
  name: string;
  state: string;
  asn?: string;
}

export interface HiddenAssocChildInfo {
  dxGatewayId: string;
  dxGatewayName: string;
  state: string;
  reason: 'prefixPool';
}

export interface DxNodeData extends Record<string, unknown> {
  label: string;
  category: NodeCategory;
  isRecommended?: boolean;
  isInferred?: boolean;
  isOrphan?: boolean;
  // Orphan VPCs (no TGW/VGW/Cloud WAN attachment) and isolated TGWs
  // (no attachments of any kind) — marked so the "Show unattached" toolbar
  // toggle can hide them en masse without touching the rest of the graph.
  isUnattached?: boolean;
  resourceId?: string;
  details?: Record<string, string>;
  badges?: NodeBadge[];
  childCount?: number;
  isExpanded?: boolean;
  targetHandleIds?: string[];
  // True when a VPN Connection terminates on this gateway and the top
  // handle should render. Unattached VGWs/TGWs omit this so they don't
  // show a disconnected dot above the node.
  hasTopHandle?: boolean;
  // TGWs involved in a TGW↔TGW or Cloud WAN↔TGW peering need named left
  // handles for the peering edges to anchor to. Flagged per-node so the
  // handles only render when an edge actually references them — otherwise
  // ReactFlow picks the left source handle ahead of the default Right
  // source handle for unqualified edges (e.g. TGW→VPC).
  hasPeeringHandle?: boolean;
  vpcChildren?: VpcChildInfo[];
  tgwChildren?: TgwChildInfo[];
  vgwChildren?: VgwChildInfo[];
  dxgwChildren?: DxgwChildInfo[];
  hiddenAssocChildren?: HiddenAssocChildInfo[];
  // Total VPCs reachable only via non-DX TGWs/VGWs — drives the Region header
  // "Show/Hide non-DX" toggle label. Whether they're currently hidden is
  // derived from `showNonDxVpcs.has(regionCode)` in the store.
  nonDxVpcCount?: number;
}

export interface NodeBadge {
  type: 'warning' | 'info' | 'error';
  label: string;
  description: string;
}

export type DxNode = Node<DxNodeData>;
export type DxEdge = Edge & {
  data?: {
    isRecommended?: boolean;
    isInferred?: boolean;
    vifType?: 'private' | 'transit' | 'public';
    vlan?: number;
    label?: string;
    connectionId?: string;
    connectionState?: string;
    tunnels?: VpnTunnel[];
    vifState?: string;
    bgpStatus?: string;
    vifId?: string;
    prefixesAccepted?: number;
    prefixesAdvertised?: number;
    // Peak hourly bitrate over the user-selected window (30/60/90 days) from
    // CloudWatch (AWS/DX namespace). Populated only when "Show utilization"
    // is on. Surfaced on edges anchored to a VIF (private/transit/public)
    // and on edges that represent a DX Connection segment.
    utilizationIngressBps?: number;
    utilizationEgressBps?: number;
    // Underlying connection bandwidth string (e.g. "1Gbps") — used to format
    // utilization as a percentage of capacity on VIF edges.
    connectionBandwidth?: string;
    labelPosition?: number;
    sourceHandle?: string;
    targetHandle?: string;
    // 'smoothstep' routes the edge with horizontal + vertical legs and
    // a bend radius. Used for TGW↔TGW peering and Cloud WAN→TGW peering
    // so the vertical leg can be pushed out to clear region containers.
    edgeStyle?: 'smoothstep';
  };
};
