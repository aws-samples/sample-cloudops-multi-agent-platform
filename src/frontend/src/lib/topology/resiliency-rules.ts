import type { TopologyData, DxNode, DxEdge } from './topology-types';
import type { Recommendation, ResiliencyLevel } from './recommendations';
import { COLORS } from './colors';
import { getLocationDeviceCounts } from './sla-gating';

export type ResiliencyTarget = Extract<ResiliencyLevel, 'high' | 'maximum'>;

function makeGhostNode(id: string, category: string, label: string, extra?: Record<string, unknown>): DxNode {
  return {
    id,
    type: category,
    position: { x: 0, y: 0 },
    data: { label, category: category as DxNode['data']['category'], isRecommended: true, ...extra },
  };
}

function makeGhostEdge(source: string, target: string, label?: string, labelPosition?: number): DxEdge {
  return {
    id: `e-rec-${source}-${target}`,
    source,
    target,
    type: 'customEdge',
    data: { isRecommended: true, label, ...(labelPosition != null ? { labelPosition } : {}) },
    style: { stroke: COLORS.recommended.edge },
  };
}

export function ruleSingleDxLocation(
  topology: TopologyData,
  target: ResiliencyTarget = 'high',
  dxGatewayId?: string,
  dxGatewayName?: string,
): Recommendation | null {
  const usedLocations = new Set<string>();
  for (const conn of topology.connections) {
    if (conn.location) usedLocations.add(conn.location);
  }
  if (usedLocations.size === 0) {
    for (const vif of topology.virtualInterfaces) {
      if (vif.location) usedLocations.add(vif.location);
    }
  }
  if (usedLocations.size >= 2) return null;
  if (usedLocations.size === 0) return null;

  const resolvedDxgwId = dxGatewayId ?? topology.dxGateways[0]?.directConnectGatewayId;
  const dxgwNodeId = resolvedDxgwId ? `dxgw-${resolvedDxgwId}` : undefined;
  const prefix = resolvedDxgwId ? `rec-${resolvedDxgwId}` : 'rec';
  const locCode = `${prefix}-loc-B`;

  const siteLabel = dxGatewayId
    ? `Customer Data Center to support ${dxGatewayName ?? dxGatewayId}`
    : 'Customer Data Center';
  const nodes: DxNode[] = [
    makeGhostNode(`${prefix}-custsite-B`, 'customerSite', siteLabel, { details: { locationCode: locCode, dxGatewayId } }),
    makeGhostNode(`${prefix}-onprem-B`, 'onPremise', 'Customer Gateway'),
    makeGhostNode(`${prefix}-dxloc-B`, 'dxLocation', 'Second Direct Connect Location', { details: { code: locCode } }),
    makeGhostNode(`${prefix}-partner-B`, 'dxPartnerDevice', 'Customer / Partner Device', { details: { locationCode: locCode } }),
    makeGhostNode(`${prefix}-awsdev-B`, 'awsDevice', 'AWS Device', { details: { locationCode: locCode } }),
  ];

  const edges: DxEdge[] = [
    makeGhostEdge(`${prefix}-onprem-B`, `${prefix}-partner-B`, undefined),
    makeGhostEdge(`${prefix}-partner-B`, `${prefix}-awsdev-B`),
  ];

  if (dxgwNodeId) {
    edges.push(makeGhostEdge(`${prefix}-awsdev-B`, dxgwNodeId, 'VIF', 0.2));
  }

  if (target === 'maximum') {
    nodes.push(
      makeGhostNode(`${prefix}-partner-B-2`, 'dxPartnerDevice', 'Customer / Partner Device', { details: { locationCode: locCode } }),
      makeGhostNode(`${prefix}-awsdev-B-2`, 'awsDevice', 'AWS Device', { details: { locationCode: locCode } }),
    );
    edges.push(
      makeGhostEdge(`${prefix}-onprem-B`, `${prefix}-partner-B-2`, undefined),
      makeGhostEdge(`${prefix}-partner-B-2`, `${prefix}-awsdev-B-2`),
    );
    if (dxgwNodeId) {
      edges.push(makeGhostEdge(`${prefix}-awsdev-B-2`, dxgwNodeId, 'VIF', 0.2));
    }
  }

  const slaLabel = target === 'maximum' ? 'Maximum Resiliency (99.99% SLA)' : 'High Resiliency (99.9% SLA)';
  const description = target === 'maximum'
    ? `Your topology uses only one Direct Connect location. Adding a second location with two redundant connections provides ${slaLabel} by eliminating both site and device failure.`
    : `Your topology uses only one Direct Connect location. Adding a second location provides ${slaLabel} by eliminating single-site failure.`;

  return {
    id: `rec-single-dx-location${resolvedDxgwId ? `-${resolvedDxgwId}` : ''}`,
    ruleId: 'single-dx-location',
    category: 'resiliency',
    severity: 'info',
    title: 'Add a Second Direct Connect Location',
    description,
    additionalNodes: nodes,
    additionalEdges: edges,
  };
}

export function ruleSingleConnectionPerLocation(
  topology: TopologyData,
  target: ResiliencyTarget = 'high',
  dxGatewayId?: string,
): Recommendation[] {
  if (target === 'high') return [];

  const recs: Recommendation[] = [];
  const locationDevices = getLocationDeviceCounts(topology);

  const resolvedDxgwId = dxGatewayId ?? topology.dxGateways[0]?.directConnectGatewayId;
  const prefix = resolvedDxgwId ? `rec-${resolvedDxgwId}` : 'rec';
  const dxgwNodeId = resolvedDxgwId ? `dxgw-${resolvedDxgwId}` : undefined;

  for (const [location, deviceCount] of locationDevices) {
    if (deviceCount >= 2) continue;

    const locNode = topology.locations.find((l) => l.locationCode === location);
    const locName = locNode?.locationName ?? location;

    const nodes: DxNode[] = [
      makeGhostNode(`${prefix}-partner-${location}-2`, 'dxPartnerDevice', 'Customer / Partner Device', { details: { locationCode: location } }),
      makeGhostNode(`${prefix}-awsdev-${location}-2`, 'awsDevice', 'AWS Device', { details: { locationCode: location } }),
    ];

    const onPremId = `onprem-${location}`;
    const edges: DxEdge[] = [
      makeGhostEdge(onPremId, `${prefix}-partner-${location}-2`, undefined),
      makeGhostEdge(`${prefix}-partner-${location}-2`, `${prefix}-awsdev-${location}-2`),
    ];

    if (dxgwNodeId) {
      edges.push(makeGhostEdge(`${prefix}-awsdev-${location}-2`, dxgwNodeId, 'VIF', 0.2));
    }

    const rawConnCount = topology.connections.length > 0
      ? topology.connections.filter((c) => c.location === location).length
      : topology.virtualInterfaces.filter((v) => (v.location ?? '') === location).length;
    const description = rawConnCount >= 2
      ? `Location ${locName} has ${rawConnCount} connections, but they terminate on the same AWS logical device — a device failure cuts this location entirely. Add a connection on a separate device to reach Maximum Resiliency (99.99% SLA).`
      : `Location ${locName} has only one Direct Connect connection. Adding a second connection on a separate device provides Maximum Resiliency (99.99% SLA).`;

    recs.push({
      id: `rec-single-conn-${location}${resolvedDxgwId ? `-${resolvedDxgwId}` : ''}`,
      ruleId: 'single-connection-per-location',
      category: 'resiliency',
      severity: 'info',
      title: `Add Redundant Connection at ${locName}`,
      description,
      additionalNodes: nodes,
      additionalEdges: edges,
    });
  }

  return recs;
}

export function ruleNoTgw(topology: TopologyData): Recommendation | null {
  if (topology.transitGateways.length > 0) return null;
  if (topology.vpnGateways.length === 0) return null;

  return {
    id: 'rec-no-tgw',
    ruleId: 'no-tgw',
    category: 'resiliency',
    severity: 'warning',
    title: 'Consider Using Transit Gateway',
    description:
      'Using a Transit Gateway instead of multiple Virtual Private Gateways simplifies routing and enables better scalability.',
    additionalNodes: [],
    additionalEdges: [],
  };
}

export function ruleSingleVgw(topology: TopologyData): Recommendation | null {
  if (topology.vpnGateways.length !== 1 || topology.transitGateways.length > 0) return null;

  return {
    id: 'rec-single-vgw',
    ruleId: 'single-vgw',
    category: 'resiliency',
    severity: 'warning',
    title: 'Add Redundant Virtual Private Gateway',
    description: 'You have a single Virtual Private Gateway. Consider adding a second one for redundancy.',
    additionalNodes: [],
    additionalEdges: [],
  };
}

export function ruleNoLag(topology: TopologyData): Recommendation | null {
  if (topology.lags.length > 0) return null;
  if (topology.connections.length < 2) return null;

  const locationConnections = new Map<string, number>();
  for (const conn of topology.connections) {
    locationConnections.set(conn.location, (locationConnections.get(conn.location) ?? 0) + 1);
  }

  if (![...locationConnections.values()].some((c) => c >= 2)) return null;

  return {
    id: 'rec-no-lag',
    ruleId: 'no-lag',
    category: 'resiliency',
    severity: 'info',
    title: 'Consider Using LAG Groups',
    description: 'Link Aggregation Groups can bundle multiple connections for simplified management.',
    additionalNodes: [],
    additionalEdges: [],
  };
}
