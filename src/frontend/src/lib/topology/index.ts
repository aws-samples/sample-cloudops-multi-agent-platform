/** Barrel export for topology types + store.
 *
 * Keep the internal file split (aws-resources / recommendations / topology-types)
 * so future diffs against the source stay sane, but consumers import one place.
 */

export type {
  DxConnection,
  BgpPeer,
  DxVirtualInterface,
  DxGateway,
  DxGatewayAssociation,
  DxLocation,
  DxLag,
  Vpc,
  VpnGateway,
  TransitGateway,
  TransitGatewayAttachment,
  TransitGatewayPeeringAttachment,
  VpnTunnel,
  VpnConnection,
  CustomerGateway,
  CloudWanCoreNetwork,
  CloudWanAttachment,
  CloudWanPeering,
  CloudWanRoute,
  CloudWanSegmentRoutes,
  TgwRouteTable,
  TgwRoute,
  TgwRouteTableWithRoutes,
  VpcRoute,
  VpcRouteTable,
  DxMaintenanceEvent,
  AwsCredentials,
} from "./aws-resources";

export type {
  TopologyData,
  ViewMode,
  GraphData,
  NodeCategory,
  VpcChildInfo,
  TgwChildInfo,
  VgwChildInfo,
  DxgwChildInfo,
  HiddenAssocChildInfo,
  DxNodeData,
  NodeBadge,
  DxNode,
  DxEdge,
} from "./topology-types";

export type {
  ResiliencyLevel,
  Recommendation,
  ResiliencyAssessment,
  BestPracticeAssessment,
  NodeAnnotation,
  DxGatewayAssessment,
  GlobalAssessment,
  CombinedAssessment,
} from "./recommendations";

export type { ResiliencyTarget } from "./resiliency-rules";
export { analyzeTopology, getRecommendedGraph } from "./recommendation-engine";
export { getAllBestPracticeResults } from "./bestpractice-rules";
