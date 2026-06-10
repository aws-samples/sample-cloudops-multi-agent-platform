/**
 * Zustand store for the network-resilience visualizer.
 *
 * Ported from the source SPA's `topology-store.ts` — retains the field/action
 * shape the 19 node components depend on (theme, failure simulation, hover
 * highlight, localStorage-backed user overrides), but strips chat/SSO/
 * credentials/bedrock state that the parent app already handles elsewhere.
 *
 * Scope: strictly visualizer-local. First Zustand store in the parent app —
 * do NOT refactor other panels to use it.
 *
 * `theme` is mirrored from the parent's `ThemeProvider` via `VisualizerPanel`
 * instead of owning its own localStorage key, so the visualizer stays in sync
 * with the app-wide theme toggle.
 *
 * All localStorage access is SSR-guarded — Next.js builds the first paint on
 * the server, where `window` is undefined.
 */

"use client";

import { create } from "zustand";
import type {
  DxNode,
  DxEdge,
  TopologyData,
  ViewMode,
  CombinedAssessment,
} from "./index";

export type ResiliencyTarget = "high" | "maximum";

const SIZE_STORAGE_KEY = "dx-viz-node-sizes";
const REWIRE_STORAGE_KEY = "dx-viz-edge-rewires";
const HIDDEN_EDGES_KEY = "dx-viz-hidden-edges";
const USER_EDGES_KEY = "dx-viz-user-edges";
const USER_CUSTOMER_SITES_KEY = "dx-viz-user-customer-sites";
const HIDDEN_CUSTOMER_SITES_KEY = "dx-viz-hidden-customer-sites";
const USER_ONPREMISES_KEY = "dx-viz-user-onpremises";
const HIDDEN_ONPREMISES_KEY = "dx-viz-hidden-onpremises";

const hasWindow = () => typeof window !== "undefined";

function loadMapFromStorage<V>(key: string): Map<string, V> {
  if (!hasWindow()) return new Map();
  try {
    const raw = localStorage.getItem(key);
    if (raw) return new Map(Object.entries(JSON.parse(raw) as Record<string, V>));
  } catch {
    /* ignore */
  }
  return new Map();
}

function saveMapToStorage<V>(key: string, map: Map<string, V>) {
  if (!hasWindow()) return;
  try {
    localStorage.setItem(key, JSON.stringify(Object.fromEntries(map)));
  } catch {
    /* ignore */
  }
}

function loadSetFromStorage(key: string): Set<string> {
  if (!hasWindow()) return new Set();
  try {
    const raw = localStorage.getItem(key);
    if (raw) return new Set(JSON.parse(raw) as string[]);
  } catch {
    /* ignore */
  }
  return new Set();
}

function saveSetToStorage(key: string, set: Set<string>) {
  if (!hasWindow()) return;
  try {
    localStorage.setItem(key, JSON.stringify([...set]));
  } catch {
    /* ignore */
  }
}

function loadArrayFromStorage<T>(key: string): T[] {
  if (!hasWindow()) return [];
  try {
    const raw = localStorage.getItem(key);
    if (raw) return JSON.parse(raw) as T[];
  } catch {
    /* ignore */
  }
  return [];
}

function saveArrayToStorage<T>(key: string, arr: T[]) {
  if (!hasWindow()) return;
  try {
    localStorage.setItem(key, JSON.stringify(arr));
  } catch {
    /* ignore */
  }
}

// Node-size overrides are intentionally in-memory only — a browser refresh
// reverts the Customer Data Center zone to its auto-computed layout size.
// Auto-sizing reacts to topology shape changes (new VPN router, hidden site)
// and a stale persisted override from a different topology shape can crop
// content or leave empty space. See
// `temp/nr-node-size-overrides-not-persisted.md` for options if we want to
// re-introduce persistence later (topology-keyed, or explicit reset menu).
if (hasWindow()) {
  try {
    localStorage.removeItem(SIZE_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

type AdjMaps = {
  incoming: Map<string, { edgeId: string; source: string }[]>;
  outgoing: Map<string, { edgeId: string; target: string }[]>;
};
const currentAdjCache = new WeakMap<DxEdge[], WeakMap<DxEdge[], AdjMaps>>();
const recommendedAdjCache = new WeakMap<DxEdge[], WeakMap<DxEdge[], WeakMap<DxEdge[], AdjMaps>>>();

function buildAdjMaps(edges: DxEdge[]): AdjMaps {
  const incoming = new Map<string, { edgeId: string; source: string }[]>();
  const outgoing = new Map<string, { edgeId: string; target: string }[]>();
  for (const e of edges) {
    if (!incoming.has(e.target)) incoming.set(e.target, []);
    if (!outgoing.has(e.source)) outgoing.set(e.source, []);
    incoming.get(e.target)!.push({ edgeId: e.id, source: e.source });
    outgoing.get(e.source)!.push({ edgeId: e.id, target: e.target });
  }
  return { incoming, outgoing };
}

function getAdjMaps(
  viewMode: ViewMode,
  currentEdges: DxEdge[],
  recommendedEdges: DxEdge[],
  userEdges: DxEdge[],
): AdjMaps {
  if (viewMode !== "recommended") {
    let inner = currentAdjCache.get(currentEdges);
    if (!inner) {
      inner = new WeakMap<DxEdge[], AdjMaps>();
      currentAdjCache.set(currentEdges, inner);
    }
    let maps = inner.get(userEdges);
    if (!maps) {
      maps = buildAdjMaps([...currentEdges, ...userEdges]);
      inner.set(userEdges, maps);
    }
    return maps;
  }
  let mid = recommendedAdjCache.get(currentEdges);
  if (!mid) {
    mid = new WeakMap<DxEdge[], WeakMap<DxEdge[], AdjMaps>>();
    recommendedAdjCache.set(currentEdges, mid);
  }
  let inner = mid.get(recommendedEdges);
  if (!inner) {
    inner = new WeakMap<DxEdge[], AdjMaps>();
    mid.set(recommendedEdges, inner);
  }
  let maps = inner.get(userEdges);
  if (!maps) {
    maps = buildAdjMaps([...currentEdges, ...recommendedEdges, ...userEdges]);
    inner.set(userEdges, maps);
  }
  return maps;
}

// End-to-end path for a given node. Edges in this graph are directed left-to-right:
// on-prem → partner → AWS device → DXGW → TGW/VGW → VPC (and VPN → VGW).
// To avoid pulling in sibling branches at shared hubs (e.g. both DXGW and VPN
// feed into the same VGW), we traverse directionally — upstream follows
// target→source, downstream follows source→target. A middle node therefore
// shows only its own E2E path, not every path sharing the same hub.
function computePath(
  id: string,
  state: { viewMode: ViewMode; currentEdges: DxEdge[]; recommendedEdges: DxEdge[]; userEdges: DxEdge[] },
): { nodes: Set<string>; edges: Set<string> } {
  const { incoming, outgoing } = getAdjMaps(state.viewMode, state.currentEdges, state.recommendedEdges, state.userEdges);
  const nodes = new Set<string>([id]);
  const edges = new Set<string>();
  const upQueue: string[] = [id];
  while (upQueue.length > 0) {
    const n = upQueue.shift()!;
    const preds = incoming.get(n);
    if (!preds) continue;
    for (const { edgeId, source } of preds) {
      edges.add(edgeId);
      if (!nodes.has(source)) {
        nodes.add(source);
        upQueue.push(source);
      }
    }
  }
  const downQueue: string[] = [id];
  while (downQueue.length > 0) {
    const n = downQueue.shift()!;
    const succs = outgoing.get(n);
    if (!succs) continue;
    for (const { edgeId, target } of succs) {
      edges.add(edgeId);
      if (!nodes.has(target)) {
        nodes.add(target);
        downQueue.push(target);
      }
    }
  }
  return { nodes, edges };
}

interface TopologyStore {
  topologyData: TopologyData | null;
  setTopologyData: (data: TopologyData | null) => void;

  currentNodes: DxNode[];
  currentEdges: DxEdge[];
  setCurrentGraph: (nodes: DxNode[], edges: DxEdge[]) => void;

  recommendedNodes: DxNode[];
  recommendedEdges: DxEdge[];
  recommendedCurrentNodes: DxNode[];
  setRecommendedGraph: (nodes: DxNode[], edges: DxEdge[], currentForRec?: DxNode[]) => void;

  viewMode: ViewMode;
  setViewMode: (mode: ViewMode) => void;

  assessment: CombinedAssessment | null;
  setAssessment: (assessment: CombinedAssessment | null) => void;

  resiliencyTargets: Record<string, ResiliencyTarget>;
  setResiliencyTarget: (dxGatewayId: string, target: ResiliencyTarget) => void;

  focusedDxGatewayId: string | null;
  setFocusedDxGatewayId: (id: string | null) => void;

  theme: "dark" | "light";
  setTheme: (theme: "dark" | "light") => void;

  updateNodePositions: (changes: { id: string; position: { x: number; y: number } }[]) => void;

  expandedVpcGroups: Set<string>;
  toggleVpcGroup: (regionId: string) => void;

  vpcGroupViewMode: Map<string, "table">;
  toggleVpcGroupTable: (groupKey: string) => void;

  expandedTgwGroups: Set<string>;
  toggleTgwGroup: (regionId: string) => void;

  expandedPartnerGroups: Set<string>;
  togglePartnerGroup: (groupKey: string) => void;

  // Region codes where the user has opted in to showing VPCs reachable only
  // via non-DX TGWs/VGWs (hidden by default).
  showNonDxVpcs: Set<string>;
  toggleShowNonDxVpcs: (regionCode: string) => void;

  expandedIsolatedTgwGroups: Set<string>;
  toggleIsolatedTgwGroup: (regionId: string) => void;

  isolatedTgwGroupViewMode: Map<string, "table">;
  toggleIsolatedTgwGroupTable: (groupKey: string) => void;

  expandedTgwRoutePanels: Set<string>;
  toggleTgwRoutePanel: (tgwId: string) => void;

  expandedVpcRoutePanels: Set<string>;
  toggleVpcRoutePanel: (vpcId: string) => void;

  expandedCloudWanRoutePanels: Set<string>;
  toggleCloudWanRoutePanel: (coreNetworkId: string) => void;

  showVpcs: boolean;
  setShowVpcs: (show: boolean) => void;

  expandedUnattachedZone: boolean;
  toggleUnattachedZone: () => void;

  expandedHiddenAssocZone: boolean;
  toggleHiddenAssocZone: () => void;

  showLiveStatus: boolean;
  toggleLiveStatus: () => void;

  // Show DX utilization (peak hourly bps over a 30/60/90 day window) on edges.
  // Surfaces only when Live Status is also on — utilization shares the
  // "live operational data" mental model with the BGP poll. Each fetch bills
  // CloudWatch GetMetricData per stream, so results are cached per-window.
  showUtilization: boolean;
  toggleUtilization: () => void;

  utilizationWindow: 30 | 60 | 90;
  setUtilizationWindow: (window: 30 | 60 | 90) => void;

  // Per-window cache: keyed by `${region}:${windowDays}` so flipping
  // 30↔60↔90 within a session reuses the prior fetch instead of re-billing.
  utilizationCache: Map<
    string,
    {
      vif: Map<string, { ingressBpsPeak?: number; egressBpsPeak?: number }>;
      connection: Map<string, { ingressBpsPeak?: number; egressBpsPeak?: number }>;
    }
  >;
  setUtilizationCacheEntry: (
    key: string,
    entry: {
      vif: Map<string, { ingressBpsPeak?: number; egressBpsPeak?: number }>;
      connection: Map<string, { ingressBpsPeak?: number; egressBpsPeak?: number }>;
    },
  ) => void;
  clearUtilizationCache: () => void;

  edgeLabelOffsets: Map<string, { dx: number; dy: number }>;
  setEdgeLabelOffset: (edgeId: string, dx: number, dy: number) => void;

  nodeSizeOverrides: Map<string, { width: number; height: number }>;
  setNodeSizeOverride: (nodeId: string, width: number, height: number) => void;
  clearNodeSizeOverrides: () => void;
  updateNodeDimensions: (changes: { id: string; width: number; height: number }[]) => void;

  edgeReconnectOverrides: Map<string, { source: string; target: string }>;
  setEdgeReconnectOverride: (edgeId: string, source: string, target: string) => void;
  clearEdgeReconnectOverrides: () => void;

  hiddenEdgeIds: Set<string>;
  hideEdge: (edgeId: string) => void;
  unhideEdge: (edgeId: string) => void;
  clearHiddenEdges: () => void;

  userEdges: DxEdge[];
  addUserEdge: (edge: DxEdge) => void;
  clearUserEdges: () => void;

  userCustomerSites: DxNode[];
  addUserCustomerSite: () => void;
  removeUserCustomerSite: (id: string) => void;
  updateUserCustomerSitePosition: (id: string, position: { x: number; y: number }) => void;
  updateUserCustomerSiteDimensions: (id: string, width: number, height: number) => void;
  clearUserCustomerSites: () => void;

  // User-hidden AWS-derived Customer Data Center zones — the × button on a
  // real (non-user-created) site adds its id here so FlowCanvas filters it
  // out of the rendered graph.
  hiddenCustomerSiteIds: Set<string>;
  hideCustomerSite: (id: string) => void;
  unhideCustomerSite: (id: string) => void;
  clearHiddenCustomerSites: () => void;

  // User-created Customer Router nodes (added via the + button on an
  // existing router). Each one lives inside a Customer Data Center zone
  // identified by parentSiteId.
  userOnPremises: DxNode[];
  addUserOnPremise: (parentSiteId: string) => void;
  removeUserOnPremise: (id: string) => void;
  updateUserOnPremisePosition: (id: string, position: { x: number; y: number }) => void;

  // User-hidden AWS-derived Customer Router nodes (real routers removed
  // via the × button — we keep the underlying topology data but drop the
  // node + any edges touching it from the rendered graph).
  hiddenOnPremiseIds: Set<string>;
  hideOnPremise: (id: string) => void;
  unhideOnPremise: (id: string) => void;
  clearHiddenOnPremises: () => void;

  isSimulating: boolean;
  setIsSimulating: (simulating: boolean) => void;
  failedNodeIds: Set<string>;
  failedEdgeIds: Set<string>;
  toggleNodeFailure: (id: string) => void;
  toggleEdgeFailure: (id: string) => void;
  failZone: (nodeIds: string[], edgeIds: string[]) => void;
  clearFailures: () => void;

  homeAccountName: string | null;
  setHomeAccountName: (name: string | null) => void;

  isLocked: boolean;
  setIsLocked: (locked: boolean) => void;

  hoveredNodeId: string | null;
  highlightedNodeIds: Set<string>;
  highlightedEdgeIds: Set<string>;
  setHoveredNode: (id: string | null) => void;

  // Pinned path: a clicked node freezes the hover-highlight so it survives
  // mouse-leave. `setHoveredNode` is a no-op while a pin is active, so the
  // pinned path stays lit until the user clicks the pane or the same node
  // again. Only one pin at a time.
  pinnedNodeId: string | null;
  setPinnedNode: (id: string | null) => void;

  spotlightNodeIds: Set<string>;
  setSpotlightNode: (id: string | null) => void;
  setSpotlightNodes: (ids: Iterable<string>) => void;

  // Edge counterpart to spotlightNodeIds — used when a maintenance notice
  // refers to a VIF (dxvif-*), so the UI can glow the actual VIF edge
  // instead of the DX Gateway node it terminates on.
  spotlightEdgeIds: Set<string>;
  setSpotlightEdge: (id: string | null) => void;

  reset: () => void;
}

const initialTheme: "dark" | "light" =
  hasWindow() && document.documentElement.classList.contains("dark") ? "dark" : "light";

/** Convenience: true when the visualizer's mirrored theme is 'light'. */
export function useIsLight() {
  return useTopologyStore((s) => s.theme) === "light";
}

export const useTopologyStore = create<TopologyStore>((set) => ({
  topologyData: null,
  setTopologyData: (data) => set({ topologyData: data }),

  currentNodes: [],
  currentEdges: [],
  setCurrentGraph: (nodes, edges) =>
    set((state) => {
      const base = { currentNodes: nodes, currentEdges: edges };
      // If the user has a pinned path, the adjacency graph just changed under
      // it (e.g. expanding a collapsed group surfaces new child nodes/edges).
      // Recompute the BFS so the freshly-revealed children inherit the path
      // highlight instead of appearing dimmed.
      if (state.pinnedNodeId != null) {
        const next = { ...state, ...base };
        const path = computePath(state.pinnedNodeId, next);
        return { ...base, highlightedNodeIds: path.nodes, highlightedEdgeIds: path.edges };
      }
      return base;
    }),

  recommendedNodes: [],
  recommendedEdges: [],
  recommendedCurrentNodes: [],
  setRecommendedGraph: (nodes, edges, currentForRec) =>
    set((state) => {
      const base = {
        recommendedNodes: nodes,
        recommendedEdges: edges,
        recommendedCurrentNodes: currentForRec ?? [],
      };
      if (state.pinnedNodeId != null) {
        const next = { ...state, ...base };
        const path = computePath(state.pinnedNodeId, next);
        return { ...base, highlightedNodeIds: path.nodes, highlightedEdgeIds: path.edges };
      }
      return base;
    }),

  viewMode: "current",
  setViewMode: (mode) =>
    set((state) => ({
      viewMode: mode,
      focusedDxGatewayId: mode === "current" ? null : state.focusedDxGatewayId,
    })),

  assessment: null,
  setAssessment: (assessment) => set({ assessment }),

  resiliencyTargets: {},
  setResiliencyTarget: (dxGatewayId, target) =>
    set((state) => ({
      resiliencyTargets: { ...state.resiliencyTargets, [dxGatewayId]: target },
    })),

  focusedDxGatewayId: null,
  setFocusedDxGatewayId: (id) => set({ focusedDxGatewayId: id }),

  theme: initialTheme,
  setTheme: (theme) => set({ theme }),

  updateNodePositions: (changes) =>
    set((state) => {
      const posMap = new Map(changes.map((c) => [c.id, c.position]));
      const updateList = (nodes: DxNode[]) => {
        let changed = false;
        const next = nodes.map((n) => {
          const pos = posMap.get(n.id);
          if (!pos) return n;
          changed = true;
          return { ...n, position: pos };
        });
        return changed ? next : nodes;
      };
      const currentNodes = updateList(state.currentNodes);
      const recommendedNodes = updateList(state.recommendedNodes);
      const recommendedCurrentNodes = updateList(state.recommendedCurrentNodes);
      const patch: Partial<TopologyStore> = {};
      if (currentNodes !== state.currentNodes) patch.currentNodes = currentNodes;
      if (recommendedNodes !== state.recommendedNodes) patch.recommendedNodes = recommendedNodes;
      if (recommendedCurrentNodes !== state.recommendedCurrentNodes)
        patch.recommendedCurrentNodes = recommendedCurrentNodes;
      return patch;
    }),

  expandedVpcGroups: new Set(),
  toggleVpcGroup: (regionId) =>
    set((state) => {
      const next = new Set(state.expandedVpcGroups);
      if (next.has(regionId)) next.delete(regionId);
      else next.add(regionId);
      return { expandedVpcGroups: next };
    }),

  vpcGroupViewMode: new Map(),
  toggleVpcGroupTable: (groupKey) =>
    set((state) => {
      const next = new Map(state.vpcGroupViewMode);
      if (next.has(groupKey)) next.delete(groupKey);
      else next.set(groupKey, "table");
      return { vpcGroupViewMode: next };
    }),

  expandedTgwGroups: new Set(),
  toggleTgwGroup: (regionId) =>
    set((state) => {
      const next = new Set(state.expandedTgwGroups);
      if (next.has(regionId)) next.delete(regionId);
      else next.add(regionId);
      return { expandedTgwGroups: next };
    }),

  expandedPartnerGroups: new Set(),
  togglePartnerGroup: (groupKey) =>
    set((state) => {
      const next = new Set(state.expandedPartnerGroups);
      if (next.has(groupKey)) next.delete(groupKey);
      else next.add(groupKey);
      return { expandedPartnerGroups: next };
    }),

  showNonDxVpcs: new Set(),
  toggleShowNonDxVpcs: (regionCode) =>
    set((state) => {
      const next = new Set(state.showNonDxVpcs);
      if (next.has(regionCode)) next.delete(regionCode);
      else next.add(regionCode);
      return { showNonDxVpcs: next };
    }),

  expandedIsolatedTgwGroups: new Set(),
  toggleIsolatedTgwGroup: (regionId) =>
    set((state) => {
      const next = new Set(state.expandedIsolatedTgwGroups);
      if (next.has(regionId)) next.delete(regionId);
      else next.add(regionId);
      return { expandedIsolatedTgwGroups: next };
    }),

  isolatedTgwGroupViewMode: new Map(),
  toggleIsolatedTgwGroupTable: (groupKey) =>
    set((state) => {
      const next = new Map(state.isolatedTgwGroupViewMode);
      if (next.has(groupKey)) next.delete(groupKey);
      else next.set(groupKey, "table");
      return { isolatedTgwGroupViewMode: next };
    }),

  expandedTgwRoutePanels: new Set(),
  toggleTgwRoutePanel: (tgwId) =>
    set((state) => {
      const next = new Set(state.expandedTgwRoutePanels);
      if (next.has(tgwId)) next.delete(tgwId);
      else next.add(tgwId);
      return { expandedTgwRoutePanels: next };
    }),

  expandedVpcRoutePanels: new Set(),
  toggleVpcRoutePanel: (vpcId) =>
    set((state) => {
      const next = new Set(state.expandedVpcRoutePanels);
      if (next.has(vpcId)) next.delete(vpcId);
      else next.add(vpcId);
      return { expandedVpcRoutePanels: next };
    }),

  expandedCloudWanRoutePanels: new Set(),
  toggleCloudWanRoutePanel: (coreNetworkId) =>
    set((state) => {
      const next = new Set(state.expandedCloudWanRoutePanels);
      if (next.has(coreNetworkId)) next.delete(coreNetworkId);
      else next.add(coreNetworkId);
      return { expandedCloudWanRoutePanels: next };
    }),

  showVpcs: true,
  setShowVpcs: (show) => set({ showVpcs: show }),

  expandedUnattachedZone: false,
  toggleUnattachedZone: () =>
    set((state) => ({ expandedUnattachedZone: !state.expandedUnattachedZone })),

  expandedHiddenAssocZone: false,
  toggleHiddenAssocZone: () =>
    set((state) => ({ expandedHiddenAssocZone: !state.expandedHiddenAssocZone })),

  showLiveStatus: false,
  toggleLiveStatus: () =>
    set((state) => {
      const nextLive = !state.showLiveStatus;
      // Utilization is gated behind Live Status. Auto-collapse the toggle
      // when the user turns Live off so the toolbar UI stays consistent.
      if (!nextLive) {
        return { showLiveStatus: false, showUtilization: false };
      }
      return { showLiveStatus: true };
    }),

  showUtilization: false,
  toggleUtilization: () =>
    set((state) => ({ showUtilization: !state.showUtilization })),

  utilizationWindow: 30,
  setUtilizationWindow: (window) => set({ utilizationWindow: window }),

  utilizationCache: new Map(),
  setUtilizationCacheEntry: (key, entry) =>
    set((state) => {
      const next = new Map(state.utilizationCache);
      next.set(key, entry);
      return { utilizationCache: next };
    }),
  clearUtilizationCache: () => set({ utilizationCache: new Map() }),

  edgeLabelOffsets: new Map(),
  setEdgeLabelOffset: (edgeId, dx, dy) =>
    set((state) => {
      const next = new Map(state.edgeLabelOffsets);
      next.set(edgeId, { dx, dy });
      return { edgeLabelOffsets: next };
    }),

  nodeSizeOverrides: new Map<string, { width: number; height: number }>(),
  setNodeSizeOverride: (nodeId, width, height) =>
    set((state) => {
      const next = new Map(state.nodeSizeOverrides);
      next.set(nodeId, { width, height });
      return { nodeSizeOverrides: next };
    }),
  clearNodeSizeOverrides: () => {
    set({ nodeSizeOverrides: new Map() });
  },
  updateNodeDimensions: (changes) =>
    set((state) => {
      const updateList = (nodes: DxNode[]) => {
        let changed = false;
        const next = nodes.map((n) => {
          const ch = changes.find((c) => c.id === n.id);
          if (!ch) return n;
          changed = true;
          return {
            ...n,
            width: ch.width,
            height: ch.height,
            style: { ...n.style, width: ch.width, height: ch.height },
            data: { ...n.data, containerWidth: ch.width, containerHeight: ch.height },
          };
        });
        return changed ? next : nodes;
      };
      const currentNodes = updateList(state.currentNodes);
      const recommendedCurrentNodes = updateList(state.recommendedCurrentNodes);
      const patch: Partial<TopologyStore> = {};
      if (currentNodes !== state.currentNodes) patch.currentNodes = currentNodes;
      if (recommendedCurrentNodes !== state.recommendedCurrentNodes)
        patch.recommendedCurrentNodes = recommendedCurrentNodes;
      return patch;
    }),

  edgeReconnectOverrides: loadMapFromStorage<{ source: string; target: string }>(REWIRE_STORAGE_KEY),
  setEdgeReconnectOverride: (edgeId, source, target) =>
    set((state) => {
      const next = new Map(state.edgeReconnectOverrides);
      next.set(edgeId, { source, target });
      saveMapToStorage(REWIRE_STORAGE_KEY, next);
      return { edgeReconnectOverrides: next };
    }),
  clearEdgeReconnectOverrides: () => {
    if (hasWindow()) {
      try {
        localStorage.removeItem(REWIRE_STORAGE_KEY);
      } catch {
        /* ignore */
      }
    }
    set({ edgeReconnectOverrides: new Map() });
  },

  hiddenEdgeIds: loadSetFromStorage(HIDDEN_EDGES_KEY),
  hideEdge: (edgeId) =>
    set((state) => {
      const next = new Set(state.hiddenEdgeIds);
      next.add(edgeId);
      saveSetToStorage(HIDDEN_EDGES_KEY, next);
      return { hiddenEdgeIds: next };
    }),
  unhideEdge: (edgeId) =>
    set((state) => {
      const next = new Set(state.hiddenEdgeIds);
      next.delete(edgeId);
      saveSetToStorage(HIDDEN_EDGES_KEY, next);
      return { hiddenEdgeIds: next };
    }),
  clearHiddenEdges: () => {
    if (hasWindow()) {
      try {
        localStorage.removeItem(HIDDEN_EDGES_KEY);
      } catch {
        /* ignore */
      }
    }
    set({ hiddenEdgeIds: new Set() });
  },

  userEdges: loadArrayFromStorage<DxEdge>(USER_EDGES_KEY),
  addUserEdge: (edge) =>
    set((state) => {
      const next = state.userEdges.some((e) => e.id === edge.id)
        ? state.userEdges
        : [...state.userEdges, edge];
      saveArrayToStorage(USER_EDGES_KEY, next);
      const patch: Partial<TopologyStore> = { userEdges: next };
      if (state.hiddenEdgeIds.has(edge.id)) {
        const hidden = new Set(state.hiddenEdgeIds);
        hidden.delete(edge.id);
        saveSetToStorage(HIDDEN_EDGES_KEY, hidden);
        patch.hiddenEdgeIds = hidden;
      }
      return patch;
    }),
  clearUserEdges: () => {
    if (hasWindow()) {
      try {
        localStorage.removeItem(USER_EDGES_KEY);
      } catch {
        /* ignore */
      }
    }
    set({ userEdges: [] });
  },

  userCustomerSites: loadArrayFromStorage<DxNode>(USER_CUSTOMER_SITES_KEY),
  addUserCustomerSite: () =>
    set((state) => {
      const id = `user-custsite-${Date.now()}`;
      const newSite: DxNode = {
        id,
        type: "customerSite",
        position: { x: 0, y: 0 },
        data: {
          label: "Customer Data Center",
          category: "customerSite",
          details: { userCreated: "true" },
        },
        style: { width: 260, height: 120 },
        width: 260,
        height: 120,
      };
      const next = [...state.userCustomerSites, newSite];
      saveArrayToStorage(USER_CUSTOMER_SITES_KEY, next);
      return { userCustomerSites: next };
    }),
  removeUserCustomerSite: (id) =>
    set((state) => {
      const next = state.userCustomerSites.filter((s) => s.id !== id);
      saveArrayToStorage(USER_CUSTOMER_SITES_KEY, next);
      return { userCustomerSites: next };
    }),
  updateUserCustomerSitePosition: (id, position) =>
    set((state) => {
      let changed = false;
      const next = state.userCustomerSites.map((s) => {
        if (s.id !== id) return s;
        changed = true;
        return { ...s, position, data: { ...s.data, userPlaced: "true" } };
      });
      if (!changed) return {};
      saveArrayToStorage(USER_CUSTOMER_SITES_KEY, next);
      return { userCustomerSites: next };
    }),
  updateUserCustomerSiteDimensions: (id, width, height) =>
    set((state) => {
      let changed = false;
      const next = state.userCustomerSites.map((s) => {
        if (s.id !== id) return s;
        changed = true;
        return {
          ...s,
          width,
          height,
          style: { ...s.style, width, height },
          data: { ...s.data, containerWidth: width, containerHeight: height },
        };
      });
      if (!changed) return {};
      saveArrayToStorage(USER_CUSTOMER_SITES_KEY, next);
      return { userCustomerSites: next };
    }),
  clearUserCustomerSites: () => {
    if (hasWindow()) {
      try {
        localStorage.removeItem(USER_CUSTOMER_SITES_KEY);
      } catch {
        /* ignore */
      }
    }
    set({ userCustomerSites: [] });
  },

  hiddenCustomerSiteIds: loadSetFromStorage(HIDDEN_CUSTOMER_SITES_KEY),
  hideCustomerSite: (id) =>
    set((state) => {
      const next = new Set(state.hiddenCustomerSiteIds);
      next.add(id);
      saveSetToStorage(HIDDEN_CUSTOMER_SITES_KEY, next);
      return { hiddenCustomerSiteIds: next };
    }),
  unhideCustomerSite: (id) =>
    set((state) => {
      const next = new Set(state.hiddenCustomerSiteIds);
      next.delete(id);
      saveSetToStorage(HIDDEN_CUSTOMER_SITES_KEY, next);
      return { hiddenCustomerSiteIds: next };
    }),
  clearHiddenCustomerSites: () => {
    if (hasWindow()) {
      try {
        localStorage.removeItem(HIDDEN_CUSTOMER_SITES_KEY);
      } catch {
        /* ignore */
      }
    }
    set({ hiddenCustomerSiteIds: new Set() });
  },

  userOnPremises: loadArrayFromStorage<DxNode>(USER_ONPREMISES_KEY),
  addUserOnPremise: (parentSiteId) =>
    set((state) => {
      const id = `user-onprem-${Date.now()}`;
      // Seed with (0, 0); FlowCanvas repositions on the next render based
      // on the zone's current width and sibling router positions. Matches
      // the userCustomerSite pattern where position is finalized at paint.
      const newRouter: DxNode = {
        id,
        type: "onPremise",
        parentId: parentSiteId,
        position: { x: 0, y: 0 },
        data: {
          label: "Customer Router",
          category: "onPremise",
          details: { userCreated: "true", parentSiteId },
        },
      };
      const next = [...state.userOnPremises, newRouter];
      saveArrayToStorage(USER_ONPREMISES_KEY, next);
      return { userOnPremises: next };
    }),
  removeUserOnPremise: (id) =>
    set((state) => {
      const next = state.userOnPremises.filter((r) => r.id !== id);
      saveArrayToStorage(USER_ONPREMISES_KEY, next);
      return { userOnPremises: next };
    }),
  updateUserOnPremisePosition: (id, position) =>
    set((state) => {
      let changed = false;
      const next = state.userOnPremises.map((r) => {
        if (r.id !== id) return r;
        changed = true;
        return { ...r, position, data: { ...r.data, userPlaced: "true" } };
      });
      if (!changed) return {};
      saveArrayToStorage(USER_ONPREMISES_KEY, next);
      return { userOnPremises: next };
    }),

  hiddenOnPremiseIds: loadSetFromStorage(HIDDEN_ONPREMISES_KEY),
  hideOnPremise: (id) =>
    set((state) => {
      const next = new Set(state.hiddenOnPremiseIds);
      next.add(id);
      saveSetToStorage(HIDDEN_ONPREMISES_KEY, next);
      return { hiddenOnPremiseIds: next };
    }),
  unhideOnPremise: (id) =>
    set((state) => {
      const next = new Set(state.hiddenOnPremiseIds);
      next.delete(id);
      saveSetToStorage(HIDDEN_ONPREMISES_KEY, next);
      return { hiddenOnPremiseIds: next };
    }),
  clearHiddenOnPremises: () => {
    if (hasWindow()) {
      try {
        localStorage.removeItem(HIDDEN_ONPREMISES_KEY);
      } catch {
        /* ignore */
      }
    }
    set({ hiddenOnPremiseIds: new Set() });
  },

  isSimulating: false,
  setIsSimulating: (simulating) =>
    set({
      isSimulating: simulating,
      ...(!simulating ? { failedNodeIds: new Set(), failedEdgeIds: new Set() } : {}),
    }),
  failedNodeIds: new Set(),
  failedEdgeIds: new Set(),
  toggleNodeFailure: (id) =>
    set((state) => {
      const next = new Set(state.failedNodeIds);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { failedNodeIds: next };
    }),
  toggleEdgeFailure: (id) =>
    set((state) => {
      const next = new Set(state.failedEdgeIds);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { failedEdgeIds: next };
    }),
  failZone: (nodeIds, edgeIds) =>
    set((state) => {
      const nextNodes = new Set(state.failedNodeIds);
      const nextEdges = new Set(state.failedEdgeIds);
      const allFailed = nodeIds.every((id) => nextNodes.has(id));
      if (allFailed) {
        for (const id of nodeIds) nextNodes.delete(id);
        for (const id of edgeIds) nextEdges.delete(id);
      } else {
        for (const id of nodeIds) nextNodes.add(id);
        for (const id of edgeIds) nextEdges.add(id);
      }
      return { failedNodeIds: nextNodes, failedEdgeIds: nextEdges };
    }),
  clearFailures: () => set({ failedNodeIds: new Set(), failedEdgeIds: new Set() }),

  homeAccountName: null,
  setHomeAccountName: (name) => set({ homeAccountName: name }),

  isLocked: true,
  setIsLocked: (locked) => set({ isLocked: locked }),

  hoveredNodeId: null,
  highlightedNodeIds: new Set(),
  highlightedEdgeIds: new Set(),
  pinnedNodeId: null,
  setHoveredNode: (id) =>
    set((state) => {
      // A pinned node freezes the highlight — hover in/out is a visual no-op
      // until the user explicitly unpins. Keeps the path readable while the
      // cursor wanders off to inspect details or read a side panel.
      if (state.pinnedNodeId != null) return {};
      if (id === state.hoveredNodeId) return {};
      if (id == null) {
        return {
          hoveredNodeId: null,
          highlightedNodeIds: new Set(),
          highlightedEdgeIds: new Set(),
        };
      }
      const path = computePath(id, state);
      return {
        hoveredNodeId: id,
        highlightedNodeIds: path.nodes,
        highlightedEdgeIds: path.edges,
      };
    }),
  setPinnedNode: (id) =>
    set((state) => {
      if (id == null) {
        if (state.pinnedNodeId == null) return {};
        return {
          pinnedNodeId: null,
          hoveredNodeId: null,
          highlightedNodeIds: new Set(),
          highlightedEdgeIds: new Set(),
        };
      }
      // Toggle off if clicking the already-pinned node.
      if (state.pinnedNodeId === id) {
        return {
          pinnedNodeId: null,
          hoveredNodeId: null,
          highlightedNodeIds: new Set(),
          highlightedEdgeIds: new Set(),
        };
      }
      const path = computePath(id, state);
      return {
        pinnedNodeId: id,
        hoveredNodeId: id,
        highlightedNodeIds: path.nodes,
        highlightedEdgeIds: path.edges,
      };
    }),

  spotlightNodeIds: new Set(),
  setSpotlightNode: (id) =>
    set((state) => {
      if (id == null) {
        return state.spotlightNodeIds.size === 0 ? {} : { spotlightNodeIds: new Set() };
      }
      if (state.spotlightNodeIds.size === 1 && state.spotlightNodeIds.has(id)) return {};
      return { spotlightNodeIds: new Set([id]) };
    }),
  setSpotlightNodes: (ids) =>
    set((state) => {
      const next = new Set(ids);
      if (next.size === state.spotlightNodeIds.size) {
        let same = true;
        for (const id of next) {
          if (!state.spotlightNodeIds.has(id)) {
            same = false;
            break;
          }
        }
        if (same) return {};
      }
      return { spotlightNodeIds: next };
    }),

  spotlightEdgeIds: new Set(),
  setSpotlightEdge: (id) =>
    set((state) => {
      if (id === null) {
        return state.spotlightEdgeIds.size === 0 ? {} : { spotlightEdgeIds: new Set() };
      }
      if (state.spotlightEdgeIds.size === 1 && state.spotlightEdgeIds.has(id)) return {};
      return { spotlightEdgeIds: new Set([id]) };
    }),

  reset: () =>
    set({
      topologyData: null,
      currentNodes: [],
      currentEdges: [],
      recommendedNodes: [],
      recommendedEdges: [],
      recommendedCurrentNodes: [],
      viewMode: "current",
      assessment: null,
      resiliencyTargets: {},
      focusedDxGatewayId: null,
      isSimulating: false,
      failedNodeIds: new Set(),
      failedEdgeIds: new Set(),
      hoveredNodeId: null,
      highlightedNodeIds: new Set(),
      highlightedEdgeIds: new Set(),
      spotlightNodeIds: new Set(),
      spotlightEdgeIds: new Set(),
    }),
}));

