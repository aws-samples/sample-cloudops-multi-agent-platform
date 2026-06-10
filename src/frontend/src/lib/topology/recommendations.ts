import type { DxNode, DxEdge } from './topology-types';

export type ResiliencyLevel = 'none' | 'devtest' | 'high' | 'maximum';

export interface Recommendation {
  id: string;
  ruleId: string;
  category: 'resiliency' | 'bestpractice';
  severity: 'critical' | 'warning' | 'info';
  title: string;
  description: string;
  additionalNodes: DxNode[];
  additionalEdges: DxEdge[];
}

export interface ResiliencyAssessment {
  currentLevel: ResiliencyLevel;
  targetLevel: ResiliencyLevel;
  score: number;
  recommendations: Recommendation[];
}

export interface BestPracticeAssessment {
  annotations: NodeAnnotation[];
  recommendations: Recommendation[];
}

export interface NodeAnnotation {
  nodeId: string;
  badge: {
    type: 'warning' | 'info' | 'error';
    label: string;
    description: string;
  };
}

export interface DxGatewayAssessment {
  dxGatewayId: string;
  dxGatewayName: string;
  currentLevel: ResiliencyLevel;
  targetLevel: ResiliencyLevel;
  score: number;
  locationCount: number;
  connectionCount: number;
  /** True when the DXGW has no VIFs — resiliency tiering does not apply. */
  isUnattached?: boolean;
  /** True when at least one Virtual Interface points at this DXGW. */
  hasVif?: boolean;
  /** True when the DXGW has at least one TGW/VGW association. */
  hasAssociation?: boolean;
  /** Recommendations scoped to this DX Gateway (resiliency + best-practice). */
  recommendations: Recommendation[];
}

export interface GlobalAssessment {
  resiliency: ResiliencyAssessment;
  bestPractice: BestPracticeAssessment;
}

export interface CombinedAssessment {
  /** Per-DXGW resilience cards. Empty when topology has no DX Gateways. */
  perDxGateway: DxGatewayAssessment[];
  /** Topology-wide rules that don't pin to a specific DX Gateway. */
  global: GlobalAssessment;
  /** Aggregated view of all resiliency recommendations (per-DXGW + global) for back-compat. */
  resiliency: ResiliencyAssessment;
  /** Aggregated view of all best-practice recommendations (per-DXGW + global) for back-compat. */
  bestPractice: BestPracticeAssessment;
}
