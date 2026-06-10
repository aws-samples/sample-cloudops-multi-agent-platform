import type { TopologyData, DxNode, DxEdge } from './topology-types';
import type {
  CombinedAssessment,
  ResiliencyLevel,
  Recommendation,
  DxGatewayAssessment,
} from './recommendations';
import {
  ruleSingleDxLocation,
  ruleSingleConnectionPerLocation,
  ruleNoTgw,
  ruleSingleVgw,
  ruleNoLag,
  type ResiliencyTarget,
} from './resiliency-rules';
import {
  ruleVifDown,
  ruleConnectionNotAvailable,
  ruleEnterpriseSupportRequired,
  ruleWellArchitectedReviewRequired,
  getAllBestPracticeResults,
} from './bestpractice-rules';
import { getLocationDeviceCounts } from './sla-gating';

const BASE_SCORES: Record<ResiliencyLevel, number> = {
  none: 0,
  devtest: 30,
  high: 65,
  maximum: 100,
};

function computeScore(level: ResiliencyLevel, recs: Recommendation[]): number {
  let score = BASE_SCORES[level];
  const criticalCount = recs.filter((r) => r.severity === 'critical').length;
  const warningCount = recs.filter((r) => r.severity === 'warning').length;
  score -= criticalCount * 10;
  score -= warningCount * 5;
  return Math.max(0, Math.min(100, score));
}

function determineResiliencyLevel(topology: TopologyData): ResiliencyLevel {
  if (topology.connections.length === 0 && topology.virtualInterfaces.length === 0) return 'none';

  const locationDevices = getLocationDeviceCounts(topology);
  const allLocationsHaveMultiple =
    locationDevices.size > 0 && [...locationDevices.values()].every((c) => c >= 2);

  if (locationDevices.size >= 2 && allLocationsHaveMultiple) return 'maximum';
  if (locationDevices.size >= 2) return 'high';
  if (locationDevices.size >= 1) return 'devtest';
  return 'none';
}

function buildDxgwScope(topology: TopologyData, dxGatewayId: string): TopologyData {
  const scopedVifs = topology.virtualInterfaces.filter((v) => v.directConnectGatewayId === dxGatewayId);
  const scopedConnIds = new Set(scopedVifs.map((v) => v.connectionId).filter(Boolean) as string[]);
  const scopedConns = topology.connections.filter((c) => scopedConnIds.has(c.connectionId));
  const scopedLocationCodes = new Set<string>();
  for (const c of scopedConns) if (c.location) scopedLocationCodes.add(c.location);
  for (const v of scopedVifs) if (v.location) scopedLocationCodes.add(v.location);
  const scopedLocations = topology.locations.filter((l) => scopedLocationCodes.has(l.locationCode));
  const scopedLags = topology.lags.filter((lag) =>
    lag.connections.some((c) => scopedConnIds.has(c.connectionId)),
  );

  return {
    ...topology,
    connections: scopedConns,
    virtualInterfaces: scopedVifs,
    locations: scopedLocations,
    lags: scopedLags,
  };
}

function runPerDxgwRules(
  scope: TopologyData,
  target: ResiliencyTarget,
  currentLevel: ResiliencyLevel,
  dxGatewayId: string,
  dxGatewayName?: string,
): Recommendation[] {
  const recs: Recommendation[] = [];

  const singleLocation = ruleSingleDxLocation(scope, target, dxGatewayId, dxGatewayName);
  if (singleLocation) recs.push(singleLocation);

  recs.push(...ruleSingleConnectionPerLocation(scope, target, dxGatewayId));

  const noLag = ruleNoLag(scope);
  if (noLag) recs.push(noLag);

  const vifDown = ruleVifDown(scope);
  if (vifDown.recommendation) recs.push(vifDown.recommendation);

  const connDown = ruleConnectionNotAvailable(scope);
  if (connDown.recommendation) recs.push(connDown.recommendation);

  const enterpriseSupport = ruleEnterpriseSupportRequired(scope, currentLevel, target);
  if (enterpriseSupport.recommendation) recs.push(enterpriseSupport.recommendation);

  const warReview = ruleWellArchitectedReviewRequired(scope, currentLevel, target);
  if (warReview.recommendation) recs.push(warReview.recommendation);

  const severityOrder: Record<string, number> = { critical: 0, warning: 1, info: 2 };
  recs.sort((a, b) => (severityOrder[a.severity] ?? 3) - (severityOrder[b.severity] ?? 3));
  return recs;
}

export function analyzeTopology(
  topology: TopologyData,
  targets: Record<string, ResiliencyTarget> | ResiliencyTarget = 'high',
): CombinedAssessment {
  const topLevel = determineResiliencyLevel(topology);

  const resolveTarget = (dxGatewayId: string): ResiliencyTarget => {
    if (typeof targets === 'string') return targets;
    return targets[dxGatewayId] ?? 'high';
  };

  const perDxGateway: DxGatewayAssessment[] = [];
  for (const gw of topology.dxGateways) {
    const scope = buildDxgwScope(topology, gw.directConnectGatewayId);
    const level = determineResiliencyLevel(scope);
    const userTarget = resolveTarget(gw.directConnectGatewayId);
    const effectiveTarget: ResiliencyTarget =
      level === 'maximum' ? 'maximum' : level === 'high' ? 'maximum' : userTarget;
    const hasVif = topology.virtualInterfaces.some(
      (v) => v.directConnectGatewayId === gw.directConnectGatewayId,
    );
    const hasAssociation = topology.dxGatewayAssociations.some(
      (a) => a.directConnectGatewayId === gw.directConnectGatewayId,
    );
    const isUnattached = !hasVif;
    const recs = runPerDxgwRules(
      scope,
      effectiveTarget,
      level,
      gw.directConnectGatewayId,
      gw.directConnectGatewayName,
    );
    const locationCount = new Set(
      scope.connections.map((c) => c.location).filter(Boolean) as string[],
    ).size || new Set(scope.virtualInterfaces.map((v) => v.location).filter(Boolean) as string[]).size;

    perDxGateway.push({
      dxGatewayId: gw.directConnectGatewayId,
      dxGatewayName: gw.directConnectGatewayName || gw.directConnectGatewayId,
      currentLevel: level,
      targetLevel: effectiveTarget,
      score: computeScore(level, recs),
      locationCount,
      connectionCount: scope.connections.length,
      isUnattached,
      hasVif,
      hasAssociation,
      recommendations: recs,
    });
  }

  const globalResiliencyRecs: Recommendation[] = [];

  const noTgw = ruleNoTgw(topology);
  if (noTgw) globalResiliencyRecs.push(noTgw);

  const singleVgw = ruleSingleVgw(topology);
  if (singleVgw) globalResiliencyRecs.push(singleVgw);

  const globalTarget: ResiliencyTarget = typeof targets === 'string'
    ? targets
    : topology.dxGateways[0]
      ? (targets[topology.dxGateways[0].directConnectGatewayId] ?? 'high')
      : 'high';
  const globalEffectiveTarget: ResiliencyTarget =
    topLevel === 'maximum' ? 'maximum' : topLevel === 'high' ? 'maximum' : globalTarget;

  if (topology.dxGateways.length === 0) {
    const singleLocation = ruleSingleDxLocation(topology, globalEffectiveTarget);
    if (singleLocation) globalResiliencyRecs.push(singleLocation);
    globalResiliencyRecs.push(...ruleSingleConnectionPerLocation(topology, globalEffectiveTarget));
    const noLag = ruleNoLag(topology);
    if (noLag) globalResiliencyRecs.push(noLag);
  }

  const bestPractice = getAllBestPracticeResults(topology);
  const globalBestPracticeRecs = bestPractice.recommendations.filter(
    (r) => r.ruleId !== 'vif-down' && r.ruleId !== 'connection-not-available',
  );

  const perDxgwResiliencyRecs = perDxGateway.flatMap((d) =>
    d.recommendations.filter((r) => r.category === 'resiliency'),
  );
  const perDxgwBestPracticeRecs = perDxGateway.flatMap((d) =>
    d.recommendations.filter((r) => r.category === 'bestpractice'),
  );
  const aggregateResiliencyRecs = [...perDxgwResiliencyRecs, ...globalResiliencyRecs];
  const aggregateBestPracticeRecs = [...perDxgwBestPracticeRecs, ...globalBestPracticeRecs];

  return {
    perDxGateway,
    global: {
      resiliency: {
        currentLevel: topLevel,
        targetLevel: globalEffectiveTarget,
        score: computeScore(topLevel, globalResiliencyRecs),
        recommendations: globalResiliencyRecs,
      },
      bestPractice: {
        annotations: bestPractice.annotations,
        recommendations: globalBestPracticeRecs,
      },
    },
    resiliency: {
      currentLevel: topLevel,
      targetLevel: globalEffectiveTarget,
      score: computeScore(topLevel, aggregateResiliencyRecs),
      recommendations: aggregateResiliencyRecs,
    },
    bestPractice: {
      annotations: bestPractice.annotations,
      recommendations: aggregateBestPracticeRecs,
    },
  };
}

export function getRecommendedGraph(
  assessment: CombinedAssessment,
  focusedDxGatewayId?: string | null,
): {
  nodes: DxNode[];
  edges: DxEdge[];
} {
  const nodes: DxNode[] = [];
  const edges: DxEdge[] = [];

  if (focusedDxGatewayId) {
    const match = assessment.perDxGateway.find((g) => g.dxGatewayId === focusedDxGatewayId);
    const recs = match?.recommendations.filter((r) => r.category === 'resiliency') ?? [];
    for (const rec of recs) {
      nodes.push(...rec.additionalNodes);
      edges.push(...rec.additionalEdges);
    }
    return { nodes, edges };
  }

  for (const rec of assessment.resiliency.recommendations) {
    nodes.push(...rec.additionalNodes);
    edges.push(...rec.additionalEdges);
  }

  return { nodes, edges };
}
