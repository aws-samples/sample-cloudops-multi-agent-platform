"use client";

/**
 * Drives the visualizer store from the topology + assessment props passed into
 * `VisualizerPanel`.
 *
 * Two-phase wiring:
 *
 * 1. A small bootstrap effect seeds the store from props whenever the agent
 *    ships a new topology or assessment in a chat message.
 *
 * 2. The main rebuild effect reads from the store (not props) and rebuilds
 *    the graph. This matters because `useReassess` can update
 *    `store.assessment` after a target-tier flip; the rebuild needs to pick
 *    up the new recommendations even though the prop-level assessment
 *    hasn't changed.
 */

import { useEffect } from "react";
import type {
  TopologyData,
  CombinedAssessment,
  DxNode,
  DxNodeData,
  DxEdge,
} from "@/lib/topology";
import { analyzeTopology, getRecommendedGraph } from "@/lib/topology";
import { buildGraph } from "@/lib/topology/topology-builder";
import { applyLayout } from "@/lib/topology/layout-engine";
import { useTopologyStore } from "@/lib/topology/store";

function isAssessmentUsable(a: CombinedAssessment | null | undefined): boolean {
  if (!a) return false;
  if (!Array.isArray(a.perDxGateway) || a.perDxGateway.length === 0) {
    return Boolean(a.resiliency?.recommendations?.length);
  }
  return true;
}

/** Ghost specs arrive from the Python engine as flat dicts:
 *  `{id, category, label, isRecommended, details?}` for nodes and
 *  `{id, source, target, label?, labelPosition?, isRecommended}` for edges.
 *  React Flow wants `DxNode = {id, type, position, data: {...}}`. Without
 *  this adapter, `layout-engine.ts` reads `n.data.category` on a ghost and
 *  throws because `data` is undefined — which crashes the whole panel the
 *  moment an assessment with ghost-emitting recommendations reaches it.
 */
interface FlatGhostNode {
  id: string;
  category: DxNodeData["category"];
  label: string;
  isRecommended?: boolean;
  details?: Record<string, string>;
}

interface FlatGhostEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  labelPosition?: number;
  isRecommended?: boolean;
}

function normalizeGhostNode(raw: FlatGhostNode | DxNode): DxNode {
  // Already shaped like a DxNode — pass through.
  if ("data" in raw && raw.data && typeof raw.data === "object") {
    return raw as DxNode;
  }
  const flat = raw as FlatGhostNode;
  return {
    id: flat.id,
    type: flat.category,
    position: { x: 0, y: 0 },
    data: {
      label: flat.label,
      category: flat.category,
      isRecommended: flat.isRecommended ?? true,
      ...(flat.details ? { details: flat.details } : {}),
    },
  };
}

function normalizeGhostEdge(raw: FlatGhostEdge | DxEdge): DxEdge {
  // Already has data block — trust it.
  if ("data" in raw && raw.data !== undefined) return raw as DxEdge;
  const flat = raw as FlatGhostEdge;
  return {
    id: flat.id,
    source: flat.source,
    target: flat.target,
    data: {
      isRecommended: flat.isRecommended ?? true,
      ...(flat.label !== undefined ? { label: flat.label } : {}),
      ...(flat.labelPosition !== undefined ? { labelPosition: flat.labelPosition } : {}),
    },
  };
}

/**
 * The agent runtime serializes `Map` fields as plain objects over the wire
 * (JSON has no native map). `buildGraph` + node components call `.get()` on
 * these fields — rehydrate them to real `Map` instances here so the types
 * match the runtime. Idempotent: pass-through when the field is already a Map.
 */
function rehydrateMaps(topology: TopologyData): TopologyData {
  const toMap = <V>(v: unknown): Map<string, V> => {
    if (v instanceof Map) return v as Map<string, V>;
    if (v && typeof v === "object") return new Map(Object.entries(v as Record<string, V>));
    return new Map();
  };
  return {
    ...topology,
    tgwRouteTables: toMap(topology.tgwRouteTables),
    cloudWanRoutes: toMap(topology.cloudWanRoutes),
    vpcRouteTables: toMap(topology.vpcRouteTables),
    bgpPrefixMetrics: topology.bgpPrefixMetrics
      ? toMap(topology.bgpPrefixMetrics)
      : undefined,
    regionNames: topology.regionNames ? toMap(topology.regionNames) : undefined,
    // Utilization overlays arrive as plain objects from the agent payload too;
    // `topology-builder.ts` calls `.get()` on both, so coerce them here.
    vifUtilization: topology.vifUtilization ? toMap(topology.vifUtilization) : undefined,
    connectionUtilization: topology.connectionUtilization
      ? toMap(topology.connectionUtilization)
      : undefined,
  };
}

/**
 * Extract `additionalNodes` + `additionalEdges` from every recommendation in
 * `assessment`. Source agents only populate ghosts for 2 of the 22 rules
 * (single-location and single-connection-per-location) but returning the
 * plumbing here means any future rule that emits ghosts gets rendered for
 * free. Deduplicated by id so the same ghost referenced by multiple rules
 * renders once.
 */
function collectGhosts(
  assessment: CombinedAssessment | null,
  focusedDxgwId: string | null,
): { nodes: DxNode[]; edges: DxEdge[] } {
  if (!assessment) return { nodes: [], edges: [] };
  const seenNodeIds = new Set<string>();
  const seenEdgeIds = new Set<string>();
  const nodes: DxNode[] = [];
  const edges: DxEdge[] = [];

  const pushFrom = (rec: {
    additionalNodes?: (FlatGhostNode | DxNode)[];
    additionalEdges?: (FlatGhostEdge | DxEdge)[];
  }) => {
    for (const n of rec.additionalNodes ?? []) {
      if (seenNodeIds.has(n.id)) continue;
      seenNodeIds.add(n.id);
      nodes.push(normalizeGhostNode(n));
    }
    for (const e of rec.additionalEdges ?? []) {
      if (seenEdgeIds.has(e.id)) continue;
      seenEdgeIds.add(e.id);
      edges.push(normalizeGhostEdge(e));
    }
  };

  // Per-DXGW recommendations. When focused on one DXGW, skip the others so
  // ghosts for that gateway show in isolation — matches source behavior.
  for (const gw of assessment.perDxGateway ?? []) {
    if (focusedDxgwId && gw.dxGatewayId !== focusedDxgwId) continue;
    for (const rec of gw.recommendations ?? []) pushFrom(rec);
  }
  // Global (topology-wide) recommendations — always rendered, not filtered
  // by focus since they don't pin to any single DXGW.
  for (const rec of assessment.global?.resiliency?.recommendations ?? []) pushFrom(rec);
  for (const rec of assessment.global?.bestPractice?.recommendations ?? []) pushFrom(rec);

  return { nodes, edges };
}

export function useTopologyGraph(
  topologyProp: TopologyData | null,
  assessmentProp: CombinedAssessment | null,
) {
  const topologyData = useTopologyStore((s) => s.topologyData);
  const assessment = useTopologyStore((s) => s.assessment);
  const expandedVpcGroups = useTopologyStore((s) => s.expandedVpcGroups);
  const expandedTgwGroups = useTopologyStore((s) => s.expandedTgwGroups);
  const vpcGroupViewMode = useTopologyStore((s) => s.vpcGroupViewMode);
  const expandedIsolatedTgwGroups = useTopologyStore((s) => s.expandedIsolatedTgwGroups);
  const isolatedTgwGroupViewMode = useTopologyStore((s) => s.isolatedTgwGroupViewMode);
  const showNonDxVpcs = useTopologyStore((s) => s.showNonDxVpcs);
  const expandedPartnerGroups = useTopologyStore((s) => s.expandedPartnerGroups);
  const expandedUnattachedZone = useTopologyStore((s) => s.expandedUnattachedZone);
  const expandedHiddenAssocZone = useTopologyStore((s) => s.expandedHiddenAssocZone);
  const nodeSizeOverrides = useTopologyStore((s) => s.nodeSizeOverrides);
  const focusedDxGatewayId = useTopologyStore((s) => s.focusedDxGatewayId);
  const showUtilization = useTopologyStore((s) => s.showUtilization);

  // Bootstrap: seed the store whenever the agent ships new data via props.
  // If the agent's assessment is missing or empty (no perDxGateway entries,
  // no aggregate recommendations), compute one locally with the ported TS
  // engine. This mirrors the upstream SPA behavior — recommendations and
  // ghost nodes always render, even when the runtime only ships topology.
  //
  // When only an assessment arrives (some agents call assess_dx_resiliency
  // without a paired discover_dx_topology), keep the previously-seeded
  // topology in the store rather than wiping it — otherwise the graph
  // disappears and recommendation ghosts have nothing to anchor against.
  useEffect(() => {
    const { setTopologyData, setAssessment, resiliencyTargets, topologyData: existingTopology } = useTopologyStore.getState();
    if (topologyProp) {
      const hydrated = rehydrateMaps(topologyProp);
      setTopologyData(hydrated);
      if (isAssessmentUsable(assessmentProp)) {
        setAssessment(assessmentProp);
      } else {
        const targets = Object.keys(resiliencyTargets).length > 0 ? resiliencyTargets : "high";
        setAssessment(analyzeTopology(hydrated, targets));
      }
    } else if (assessmentProp) {
      // Assessment-only payload — preserve any topology already in the store
      // (typically seeded by a prior discover call in the same conversation).
      setAssessment(assessmentProp);
    } else if (!existingTopology) {
      // No data at all and nothing to fall back to — clear.
      setTopologyData(null);
      setAssessment(null);
    }
  }, [topologyProp, assessmentProp]);

  // Rebuild graph from store state whenever topology, assessment, or any
  // expansion/size override changes.
  useEffect(() => {
    const { setCurrentGraph, setRecommendedGraph } = useTopologyStore.getState();

    if (!topologyData) {
      setCurrentGraph([], []);
      setRecommendedGraph([], [], []);
      return;
    }

    const { nodes, edges } = buildGraph(
      topologyData,
      expandedVpcGroups,
      expandedTgwGroups,
      vpcGroupViewMode,
      expandedIsolatedTgwGroups,
      isolatedTgwGroupViewMode,
      showNonDxVpcs,
      expandedPartnerGroups,
    );

    const annotations = assessment?.bestPractice.annotations ?? [];
    const nodesWithBadges: DxNode[] = nodes.map((node) => {
      const matches = annotations.filter((a) => a.nodeId === node.id);
      if (matches.length === 0) return node;
      return {
        ...node,
        data: { ...node.data, badges: matches.map((a) => a.badge) },
      };
    });

    // A Cloud WAN association counts as a downstream path but still leaves
    // the DXGW without any physical connection on the ingress side, so we
    // keep the orphan chip regardless of core-network attachments. The
    // scorecard's per-DXGW "Unattached" badge (driven by the engine's
    // isUnattached flag) communicates this case explicitly.
    const orphanDxgwIds = new Set(
      (assessment?.perDxGateway ?? [])
        .filter((d) => d.connectionCount === 0)
        .map((d) => `dxgw-${d.dxGatewayId}`),
    );
    const tagOrphans = (list: DxNode[]): DxNode[] =>
      orphanDxgwIds.size === 0
        ? list
        : list.map((n) =>
            orphanDxgwIds.has(n.id) ? { ...n, data: { ...n.data, isOrphan: true } } : n,
          );

    // Current-state layout — nodes only, no ghosts.
    const currentLayout = applyLayout(nodesWithBadges, edges, {
      expandedUnattachedZone,
      expandedHiddenAssocZone,
      nodeSizeOverrides,
      showUtilization,
    });
    setCurrentGraph(tagOrphans(currentLayout), edges);

    // Recommended-state layout — layout the combined (current + ghost) graph
    // so ghosts snap into the column system next to their anchors. Prefer
    // the engine's `getRecommendedGraph` (resiliency-only, source of truth)
    // and fall back to `collectGhosts` only if the assessment came from a
    // legacy Python payload with flat-dict additionalNodes/Edges.
    let ghostNodes: DxNode[] = [];
    let ghostEdges: DxEdge[] = [];
    if (assessment) {
      const rec = getRecommendedGraph(assessment, focusedDxGatewayId);
      // `getRecommendedGraph` pushes `additionalNodes`/`additionalEdges` raw —
      // when they arrive as flat ghost shapes ({id, category, label}, no
      // `.data`) from the agent payload, `applyLayout` crashes reading
      // `n.data.category`. Normalize here so both this path and the
      // `collectGhosts` fallback below yield real DxNode/DxEdge shapes.
      // Idempotent: already-shaped nodes/edges pass straight through.
      ghostNodes = rec.nodes.map(normalizeGhostNode);
      ghostEdges = rec.edges.map(normalizeGhostEdge);
    }
    if (ghostNodes.length === 0) {
      const fallback = collectGhosts(assessment, focusedDxGatewayId);
      ghostNodes = fallback.nodes;
      ghostEdges = fallback.edges;
    }
    if (ghostNodes.length === 0) {
      setRecommendedGraph([], [], tagOrphans(currentLayout));
      return;
    }
    const combinedNodes = [...nodesWithBadges, ...ghostNodes];
    const combinedEdges = [...edges, ...ghostEdges];
    const combinedLayout = applyLayout(combinedNodes, combinedEdges, {
      expandedUnattachedZone,
      expandedHiddenAssocZone,
      nodeSizeOverrides,
      showUtilization,
    });
    const ghostIds = new Set(ghostNodes.map((n) => n.id));
    const laidOutGhosts = combinedLayout.filter((n) => ghostIds.has(n.id));
    const laidOutCurrent = tagOrphans(combinedLayout.filter((n) => !ghostIds.has(n.id)));
    setRecommendedGraph(laidOutGhosts, ghostEdges, laidOutCurrent);
  }, [
    topologyData,
    assessment,
    expandedVpcGroups,
    expandedTgwGroups,
    vpcGroupViewMode,
    expandedIsolatedTgwGroups,
    isolatedTgwGroupViewMode,
    showNonDxVpcs,
    expandedPartnerGroups,
    expandedUnattachedZone,
    expandedHiddenAssocZone,
    nodeSizeOverrides,
    focusedDxGatewayId,
    showUtilization,
  ]);
}
