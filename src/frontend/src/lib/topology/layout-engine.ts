import type { DxNode, DxEdge } from './topology-types';
import { NODE_DIMENSIONS } from './constants';
import { ZONE_DIMS, zoneHeight } from './unattached-zone-dims';
import { HIDDEN_ASSOC_ZONE_DIMS, hiddenAssocZoneHeight } from './hidden-assoc-zone-dims';

// ---- Configuration ratios (no hardcoded pixel positions) ----
// All spacing is computed dynamically from node dimensions so the layout adapts to any topology.
const H_GAP_RATIO = 0.9;       // horizontal gap between columns as fraction of avg node width
// Gap inside AWS Cloud (dxGateway→coreNetwork→cgw→tgw_vgw). Tighter than the
// outside-cloud H_GAP_RATIO because these columns carry simple intra-cloud edges
// (DXGW→TGW) rather than the VIF/BGP labels that live on the customer-side edges.
// Clamped min/max so the strip stays readable on narrow topologies and doesn't
// balloon on wide ones (cgw/coreNetwork at 260px would inflate a pure ratio).
const CLOUD_INTERNAL_GAP_RATIO = 0.4;
const CLOUD_INTERNAL_GAP_MIN = 65;  // px — room for edge routing + any inline label
const CLOUD_INTERNAL_GAP_MAX = 130; // px — cap so wide nodes don't inflate the strip
// Column keys whose TRAILING gap is "inside AWS Cloud" and uses the tighter ratio.
// Everything else (onPremise, dxPartnerDevice, awsDevice) keeps H_GAP_RATIO so
// the customer data center ↔ DX location ↔ AWS Cloud boundaries stay roomy.
const CLOUD_INTERNAL_GAP_AFTER = new Set(['dxGateway', 'coreNetwork', 'cgw']);
// Columns whose nodes sit INSIDE a Region container. When we cross from a
// non-region column (dxGateway / coreNetwork) into one of these, the region
// container's left padding (CONTAINER_PAD_X) eats into the visible gap — so
// we inflate the column gap by CONTAINER_PAD_X to preserve the intended
// breathing room. Without this, a topology with no Cloud WAN / no VPN renders
// the DXGW flush against the region container's left edge.
const COLS_INSIDE_REGION = new Set(['cgw', 'tgw_vgw', 'tgwConnect', 'vpc']);
const V_GAP_RATIO = 0.6;       // vertical gap between rows as fraction of max node height in column
const CONTAINER_PAD_X = 40;     // container padding left & right of children
const CONTAINER_PAD_TOP = 56;   // space for container header label (~24px header + ~32px visible gap)
const CONTAINER_PAD_BOTTOM = 32; // bottom padding — matches visible gap above children so nodes center vertically
const LOC_VISUAL_GAP = 30;     // visual gap between DX location containers (pixels)
const VPN_SECTION_GAP_MIN = 0.25;   // minimum gap ratio when VPN connects to nearby column
const VPN_SECTION_GAP_PER_COL = 0.08; // additional gap per column the VPN edge spans
const VPN_SECTION_GAP_MAX_RATIO = 1.0; // cap on total gap ratio — prevents long-span edges from ballooning the empty strip above AWS Cloud

// Utilization edges (DX Connection + VIF) carry an extra row of ingress/
// egress/% text + a progress bar that's ~220px wide. Without widening the
// gap after the columns these edges land on, the label crashes into the
// next column's nodes. Applies only when the user toggles "Show utilization".
const UTIL_LABEL_GAP_MIN = 250; // px — 220 (label maxWidth) + 30 breathing room
const UTIL_GAP_AFTER = new Set(['dxPartnerDevice', 'awsDevice']);

// ---- Column definitions (DX flow left-to-right) ----
// IMPORTANT: Column widths are measured from DX nodes ONLY.
// - 'cgw' column holds VPN connection nodes; sits inside the AWS region area
//   so VPN connections wrap into the region container. Collapses when unused.
// - VPN on-prem routers are positioned independently in Step 2.5 (top strip)
//   and do NOT inflate DX column spacing.
// - Empty columns (no DX nodes) are collapsed — no width, no gap allocated.
const COLUMN_DEFS: { key: string; categories: string[] }[] = [
  { key: 'onPremise', categories: ['onPremise'] },
  { key: 'dxPartnerDevice', categories: ['dxPartnerDevice', 'dxPartnerDeviceGroup'] },
  { key: 'awsDevice', categories: ['awsDevice'] },
  { key: 'dxGateway', categories: ['dxGateway'] },
  { key: 'coreNetwork', categories: ['coreNetwork'] },
  { key: 'cgw', categories: ['cgw'] },              // VPN connection — in region
  { key: 'tgw_vgw', categories: ['tgw', 'tgwGroup', 'isolatedTgwGroup', 'vgw'] },
  { key: 'tgwConnect', categories: ['tgwConnect'] }, // dedicated column — collapses when unused
  { key: 'vpc', categories: ['vpc', 'vpcGroup'] },
];

function nodeDim(category: string, node?: DxNode) {
  if (node?.data.computedWidth || node?.data.computedHeight) {
    const base = NODE_DIMENSIONS[category] ?? { width: 120, height: 50 };
    return {
      width: (node.data.computedWidth as number) ?? base.width,
      height: (node.data.computedHeight as number) ?? base.height,
    };
  }
  return NODE_DIMENSIONS[category] ?? { width: 120, height: 50 };
}

function getLocCode(n: DxNode): string {
  return (n.data.details as Record<string, string> | undefined)?.locationCode ?? '';
}

function getContainerCode(n: DxNode): string {
  return (n.data.details as Record<string, string> | undefined)?.code ?? '';
}

// Only the on-prem VPN router is kept out of the DX flow (it's laid out in
// the top strip inside its own Customer Data Center zone). The VPN
// connection node flows through the DX/region layout so it lands inside the
// AWS region zone.
function isVpnNode(n: DxNode): boolean {
  return n.id.startsWith('onprem-vpn-');
}

// Ghost customer-site IDs come in two shapes:
//   • legacy:      rec-custsite-{suffix}           (paired with rec-onprem-{suffix})
//   • per-DXGW:    rec-{dxgwId}-custsite-B         (paired with rec-{dxgwId}-onprem-B)
// Non-ghost sites use `custsite-{loc}` paired with `onprem-{loc}`, and VPN
// sites use `custsite-vpn-{cgwId}` paired with `onprem-vpn-{cgwId}`.
function siteCompanionOnpremId(siteId: string): string | null {
  if (siteId.startsWith('rec-') && siteId.endsWith('-custsite-B')) {
    const middle = siteId.slice('rec-'.length, -'-custsite-B'.length);
    return middle ? `rec-${middle}-onprem-B` : 'rec-onprem-B';
  }
  const legacyRec = siteId.match(/^rec-custsite-(.+)$/);
  if (legacyRec) return `rec-onprem-${legacyRec[1]}`;
  const vpnSite = siteId.match(/^custsite-vpn-(.+)$/);
  if (vpnSite) return `onprem-vpn-${vpnSite[1]}`;
  const regular = siteId.match(/^custsite-(.+)$/);
  if (regular) return `onprem-${regular[1]}`;
  return null;
}

// Ghost onPremise IDs: legacy `rec-onprem-B` or per-DXGW `rec-{dxgwId}-onprem-B`.
// The matching dxLocation `locCode` (from details.code) is `rec-loc-B` / `rec-{dxgwId}-loc-B`.
function recOnpremLocCode(onpremId: string): string | null {
  if (!onpremId.startsWith('rec-')) return null;
  if (onpremId === 'rec-onprem-B') return 'rec-loc-B';
  const match = onpremId.match(/^rec-(.+)-onprem-B$/);
  return match ? `rec-${match[1]}-loc-B` : null;
}

// ---- Helpers ----
function boundingBox(nodes: DxNode[]) {
  if (nodes.length === 0) return { minX: 0, minY: 0, maxX: 0, maxY: 0 };
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of nodes) {
    const dim = nodeDim(n.data.category, n);
    minX = Math.min(minX, n.position.x);
    minY = Math.min(minY, n.position.y);
    maxX = Math.max(maxX, n.position.x + dim.width);
    maxY = Math.max(maxY, n.position.y + dim.height);
  }
  return { minX, minY, maxX, maxY };
}

function setContainer(positioned: Map<string, DxNode>, node: DxNode, x: number, y: number, w: number, h: number) {
  positioned.set(node.id, {
    ...node,
    data: { ...node.data, containerWidth: w, containerHeight: h },
    position: { x, y },
    width: w,
    height: h,
    style: { width: w, height: h },
  });
}


export function applyLayout(
  nodes: DxNode[],
  edges: DxEdge[] = [],
  opts: { expandedUnattachedZone?: boolean; expandedHiddenAssocZone?: boolean; nodeSizeOverrides?: Map<string, { width: number; height: number }>; showUtilization?: boolean } = {},
): DxNode[] {
  const { expandedUnattachedZone = false, expandedHiddenAssocZone = false, nodeSizeOverrides, showUtilization = false } = opts;
  const positioned = new Map<string, DxNode>();

  // Separate VPN nodes from DX nodes
  const vpnNodes = nodes.filter((n) => isVpnNode(n) && n.data.category !== 'dxLocation' && n.data.category !== 'region');
  const dxNodes = nodes.filter((n) => !isVpnNode(n));

  const containerCategories = new Set(['dxLocation', 'region', 'customerSite', 'awsCloud', 'unattachedZone', 'hiddenAssocZone']);
  // Unattached + hidden-assoc zones host inline tables; they have no leaf
  // children on the canvas, only UI rendered inside each container.
  const unattachedZoneContainer = dxNodes.find((n) => n.data.category === 'unattachedZone');
  const hiddenAssocZoneContainer = dxNodes.find((n) => n.data.category === 'hiddenAssocZone');
  const mainDxNodes = dxNodes.filter((n) => n.data.category !== 'unattachedZone' && n.data.category !== 'hiddenAssocZone');

  const existingLeaf = mainDxNodes.filter((n) => !n.data.isRecommended && !containerCategories.has(n.data.category));
  const recommendedLeaf = mainDxNodes.filter((n) => n.data.isRecommended && !containerCategories.has(n.data.category));
  const dxLocContainers = mainDxNodes.filter((n) => n.data.category === 'dxLocation');
  const regionContainers = mainDxNodes.filter((n) => n.data.category === 'region');

  // ---- Step 1: Measure columns (DX flow only — VPN nodes are positioned independently) ----
  const dxLeafAll = [...existingLeaf, ...recommendedLeaf];
  const colMaxWidth = new Map<string, number>();
  const colMaxHeight = new Map<string, number>();
  for (const col of COLUMN_DEFS) {
    const inCol = dxLeafAll.filter((n) => col.categories.includes(n.data.category));
    let maxW = 0, maxH = 0;
    for (const n of inCol) {
      const dim = nodeDim(n.data.category);
      maxW = Math.max(maxW, dim.width);
      maxH = Math.max(maxH, dim.height);
    }
    if (maxW === 0 && inCol.length === 0) {
      // Column has no DX nodes — collapse it (no width allocation)
      maxH = 0;
    } else if (maxW === 0) {
      const fallback = nodeDim(col.categories[0]);
      maxW = fallback.width;
      maxH = fallback.height;
    }
    colMaxWidth.set(col.key, maxW);
    colMaxHeight.set(col.key, maxH);
  }

  // ---- Step 2: Compute dynamic column X positions ----
  const nonEmptyWidths = [...colMaxWidth.values()].filter((w) => w > 0);
  const avgWidth = nonEmptyWidths.length > 0
    ? nonEmptyWidths.reduce((a, b) => a + b, 0) / nonEmptyWidths.length
    : 150;
  const hGap = avgWidth * H_GAP_RATIO;
  const cloudInternalGap = Math.max(
    CLOUD_INTERNAL_GAP_MIN,
    Math.min(CLOUD_INTERNAL_GAP_MAX, avgWidth * CLOUD_INTERNAL_GAP_RATIO),
  );

  const hasExpandedVpc = dxLeafAll.some(
    (n) => n.data.category === 'vpcGroup' && n.data.computedWidth,
  );
  const INTRA_REGION_GAP_RATIO = hasExpandedVpc ? 0.85 : 0.35;
  // Find the next non-empty column AFTER index `idx`. Needed because empty
  // columns are skipped when walking cursorX — if the next populated column
  // lives inside a region container, we need to know that for padding math.
  const nextNonEmptyColKey = (idx: number): string | null => {
    for (let j = idx + 1; j < COLUMN_DEFS.length; j++) {
      const nextKey = COLUMN_DEFS[j].key;
      if ((colMaxWidth.get(nextKey) ?? 0) > 0) return nextKey;
    }
    return null;
  };

  const colX = new Map<string, number>();
  let cursorX = 0;
  for (let i = 0; i < COLUMN_DEFS.length; i++) {
    const col = COLUMN_DEFS[i];
    const w = colMaxWidth.get(col.key) ?? 0;
    colX.set(col.key, cursorX);
    if (w > 0) {
      const nextKey = nextNonEmptyColKey(i);
      // Crossing a region container boundary — from OUTSIDE the region (DXGW
      // or CoreNetwork) into the region column. The tight `cloudInternalGap`
      // was tuned for chains where intermediate columns (coreNetwork, cgw)
      // exist between DXGW and the region; when those columns are empty the
      // region's left edge ends up flush against DXGW. Use the wider `hGap`
      // (matches the customer-side spacing) PLUS compensate for the region's
      // CONTAINER_PAD_X eating visible space.
      const crossingIntoRegion = nextKey != null
        && COLS_INSIDE_REGION.has(nextKey)
        && !COLS_INSIDE_REGION.has(col.key);

      let gap: number;
      if (col.key === 'tgw_vgw') {
        gap = avgWidth * INTRA_REGION_GAP_RATIO;
      } else if (crossingIntoRegion) {
        // DXGW/CoreNetwork directly abuts the region → outside-cloud spacing.
        gap = hGap + CONTAINER_PAD_X;
      } else if (CLOUD_INTERNAL_GAP_AFTER.has(col.key)) {
        gap = cloudInternalGap;
      } else {
        gap = hGap;
      }
      // Utilization mode widens DX Connection / VIF edge labels (extra
      // ingress/egress/% row + progress bar). Bump the gap after the columns
      // those edges land on so the label doesn't overlap the adjacent nodes.
      if (showUtilization && UTIL_GAP_AFTER.has(col.key)) {
        gap = Math.max(gap, UTIL_LABEL_GAP_MIN);
      }
      cursorX += w + gap;
    }
  }

  const nonZeroHeights = [...colMaxHeight.values()].filter((h) => h > 0);
  const globalMaxH = nonZeroHeights.length > 0 ? Math.max(...nonZeroHeights) : 80;
  const vGap = globalMaxH * V_GAP_RATIO;
  const rowHeight = globalMaxH + vGap;

  // ---- Step 2.5: Position VPN on-prem routers at the top (above DX flow) ----
  // Only the on-prem router sits in this "top strip" — the VPN connection
  // node is a regional AWS resource that flows through the main region
  // layout (so the region container wraps it). The on-prem router is
  // anchored to the 'onPremise' column X, matching where the DX
  // customer-site containers sit, so its customerSite wraps cleanly into
  // the Customer Data Center zone.
  const vpnOnPremDim = nodeDim('onPremise');
  const vpnOnPremX = colX.get('onPremise') ?? 0;
  const vpnOnPremW = colMaxWidth.get('onPremise') || vpnOnPremDim.width;

  let vpnCursorY = 0;
  const vpnOnPrems = vpnNodes.filter((n) => n.id.startsWith('onprem-vpn-'));

  for (const onPrem of vpnOnPrems) {
    // Hosted VPN routers (those with `hostSiteId`) are re-positioned by Step 7b
    // directly above their host DX site's onPremise child so both CGWs share
    // one Customer Data Center zone. They MUST NOT grow `vpnCursorY` — doing
    // so inflates `dxStartY` and leaves a tall empty strip at the top of the
    // zone (the DX flow gets shoved down for a slot that's never used).
    const hostSiteId = (onPrem.data.details as Record<string, string> | undefined)?.hostSiteId;
    if (hostSiteId) {
      positioned.set(onPrem.id, {
        ...onPrem,
        position: { x: vpnOnPremX + (vpnOnPremW - vpnOnPremDim.width) / 2, y: 0 },
      });
      continue;
    }
    positioned.set(onPrem.id, {
      ...onPrem,
      position: { x: vpnOnPremX + (vpnOnPremW - vpnOnPremDim.width) / 2, y: vpnCursorY },
    });
    vpnCursorY += rowHeight;
  }

  // Add gap between VPN section and DX section — scales with how far VPN edges travel
  const vpnSectionHeight = vpnCursorY;
  let dxStartY = 0;
  if (vpnSectionHeight > 0) {
    // Find the farthest column index that VPN nodes connect to via edges
    const vpnNodeIds = new Set(vpnNodes.map((n) => n.id));
    let maxColSpan = 2; // default: at least 2 columns away
    for (const e of edges) {
      const isVpnEdge = vpnNodeIds.has(e.source) || vpnNodeIds.has(e.target);
      if (!isVpnEdge) continue;
      const targetId = vpnNodeIds.has(e.source) ? e.target : e.source;
      const targetNode = [...dxLeafAll, ...vpnNodes].find((n: DxNode) => n.id === targetId);
      if (!targetNode) continue;
      const targetColIdx = COLUMN_DEFS.findIndex((c) => c.categories.includes(targetNode.data.category));
      if (targetColIdx > maxColSpan) maxColSpan = targetColIdx;
    }
    const gapRatio = Math.min(
      VPN_SECTION_GAP_MAX_RATIO,
      VPN_SECTION_GAP_MIN + maxColSpan * VPN_SECTION_GAP_PER_COL,
    );
    dxStartY = vpnSectionHeight + globalMaxH * gapRatio;
  }

  // ---- Step 3: Build location groups for partner/awsDevice columns ----
  const dxLeaf = [...existingLeaf, ...recommendedLeaf];
  const isPartnerCat = (cat: string) =>
    cat === 'dxPartnerDevice' || cat === 'dxPartnerDeviceGroup';

  const existingLocCodes: string[] = [];
  const existingPartners = existingLeaf.filter((n) => isPartnerCat(n.data.category));
  for (const n of existingPartners) {
    const lc = getLocCode(n);
    if (lc && !existingLocCodes.includes(lc)) existingLocCodes.push(lc);
  }

  const recLocCodes: string[] = [];
  const recPartners = recommendedLeaf.filter((n) => isPartnerCat(n.data.category));
  for (const n of recPartners) {
    const lc = getLocCode(n);
    if (lc && !existingLocCodes.includes(lc) && !recLocCodes.includes(lc)) recLocCodes.push(lc);
  }

  interface LocGroup {
    locCode: string;
    isNewLoc: boolean;
    partners: DxNode[];
    awsDevs: DxNode[];
  }

  const unorderedLocGroups: LocGroup[] = [];

  for (const lc of existingLocCodes) {
    const partners = dxLeaf.filter((n) => isPartnerCat(n.data.category) && getLocCode(n) === lc);
    const awsDevs = dxLeaf.filter((n) => n.data.category === 'awsDevice' && getLocCode(n) === lc);
    unorderedLocGroups.push({ locCode: lc, isNewLoc: false, partners, awsDevs });
  }

  for (const lc of recLocCodes) {
    const partners = recommendedLeaf.filter((n) => isPartnerCat(n.data.category) && getLocCode(n) === lc);
    const awsDevs = recommendedLeaf.filter((n) => n.data.category === 'awsDevice' && getLocCode(n) === lc);
    unorderedLocGroups.push({ locCode: lc, isNewLoc: true, partners, awsDevs });
  }

  // Interleave real and recommended locations by DX Gateway so each DXGW's
  // existing footprint is followed immediately by its ghost "second location"
  // recommendation (purple → green → purple → green). For real locations the
  // DXGW is derived from the awsDevice→dxGateway edges; for ghost locations
  // the DXGW is embedded in the locCode pattern `rec-{dxgwId}-loc-B`.
  const locGroups: LocGroup[] = (() => {
    const locToDxgwIds = new Map<string, Set<string>>();
    // Map awsDevice id → dxGateway id via edges
    const awsDevToDxgw = new Map<string, string>();
    for (const e of edges) {
      if (e.source.startsWith('awsdev-') && e.target.startsWith('dxgw-')) {
        awsDevToDxgw.set(e.source, e.target.slice('dxgw-'.length));
      }
    }
    // Map locCode → awsDevice ids by scanning dxLeaf
    for (const n of dxLeaf) {
      if (n.data.category !== 'awsDevice') continue;
      const lc = getLocCode(n);
      if (!lc) continue;
      const dxgwId = awsDevToDxgw.get(n.id);
      if (!dxgwId) continue;
      if (!locToDxgwIds.has(lc)) locToDxgwIds.set(lc, new Set());
      locToDxgwIds.get(lc)!.add(dxgwId);
    }

    const parseGhostDxgw = (lc: string): string | null => {
      const match = lc.match(/^rec-(.+)-loc-B$/);
      return match ? match[1] : null;
    };

    // Determine the stable DXGW ordering: first-appearance order in dxLeaf
    // (matches the order in topology.dxGateways since topology-builder creates
    // nodes in that order), falling back to append for unseen ids.
    const dxgwOrder: string[] = [];
    const seenDxgw = new Set<string>();
    for (const n of dxLeaf) {
      if (n.data.category !== 'awsDevice') continue;
      const dxgwId = awsDevToDxgw.get(n.id);
      if (dxgwId && !seenDxgw.has(dxgwId)) {
        seenDxgw.add(dxgwId);
        dxgwOrder.push(dxgwId);
      }
    }
    for (const g of unorderedLocGroups) {
      if (!g.isNewLoc) continue;
      const dxgwId = parseGhostDxgw(g.locCode);
      if (dxgwId && !seenDxgw.has(dxgwId)) {
        seenDxgw.add(dxgwId);
        dxgwOrder.push(dxgwId);
      }
    }

    const bucket = new Map<string, { real: LocGroup[]; rec: LocGroup[] }>();
    for (const id of dxgwOrder) bucket.set(id, { real: [], rec: [] });
    const orphan: LocGroup[] = [];

    for (const g of unorderedLocGroups) {
      if (g.isNewLoc) {
        const dxgwId = parseGhostDxgw(g.locCode);
        if (dxgwId && bucket.has(dxgwId)) {
          bucket.get(dxgwId)!.rec.push(g);
        } else {
          orphan.push(g);
        }
        continue;
      }
      // Real location: may serve multiple DXGWs — place it under its first DXGW
      // in the ordering so a location shared across DXGWs doesn't get duplicated.
      const dxgwIds = locToDxgwIds.get(g.locCode);
      const firstDxgw = dxgwIds
        ? dxgwOrder.find((id) => dxgwIds.has(id))
        : undefined;
      if (firstDxgw && bucket.has(firstDxgw)) {
        bucket.get(firstDxgw)!.real.push(g);
      } else {
        orphan.push(g);
      }
    }

    const ordered: LocGroup[] = [];
    for (const id of dxgwOrder) {
      const b = bucket.get(id);
      if (!b) continue;
      ordered.push(...b.real, ...b.rec);
    }
    ordered.push(...orphan);
    return ordered;
  })();

  // ---- Step 4: Position partner/awsDevice nodes by location group ----
  // Gap between location groups = container padding (top+bottom) so containers don't overlap + small visual gap
  const locGroupGap = CONTAINER_PAD_TOP + CONTAINER_PAD_BOTTOM + LOC_VISUAL_GAP;
  let groupCursorY = dxStartY;
  const groupYRanges: { locCode: string; startY: number; endY: number }[] = [];

  for (const group of locGroups) {
    const pCount = group.partners.length;
    const aCount = group.awsDevs.length;
    if (pCount === 0 && aCount === 0) continue;

    const groupStartY = groupCursorY;
    const partnerX = colX.get('dxPartnerDevice') ?? 0;
    const partnerColW = colMaxWidth.get('dxPartnerDevice') ?? 0;
    const awsDevX = colX.get('awsDevice') ?? 0;
    const awsDevColW = colMaxWidth.get('awsDevice') ?? 0;

    // Bipartite placement: anchor the more-populated side at rowHeight spacing,
    // then place the other side at the barycenter Y of its connected anchors.
    // A lone awsDevice opposite four partners lands on the row of the partner
    // it actually connects to (1-to-1 chains line up horizontally) instead of
    // centering in dead space.
    const partnerIds = new Set(group.partners.map((n) => n.id));
    const awsDevIds = new Set(group.awsDevs.map((n) => n.id));
    const awsToPartner = new Map<string, string[]>();
    const partnerToAws = new Map<string, string[]>();
    for (const e of edges) {
      let pId: string | null = null;
      let aId: string | null = null;
      if (partnerIds.has(e.source) && awsDevIds.has(e.target)) { pId = e.source; aId = e.target; }
      else if (partnerIds.has(e.target) && awsDevIds.has(e.source)) { pId = e.target; aId = e.source; }
      if (!pId || !aId) continue;
      const pList = partnerToAws.get(pId) ?? [];
      pList.push(aId);
      partnerToAws.set(pId, pList);
      const aList = awsToPartner.get(aId) ?? [];
      aList.push(pId);
      awsToPartner.set(aId, aList);
    }

    const packAlongTargets = (targets: number[]): number[] => {
      const order = targets.map((_, i) => i).sort((a, b) => targets[a] - targets[b]);
      const ys = new Array<number>(targets.length);
      let prev = -Infinity;
      for (const idx of order) {
        const y = prev === -Infinity ? targets[idx] : Math.max(targets[idx], prev + rowHeight);
        ys[idx] = y;
        prev = y;
      }
      return ys;
    };

    let partnerYs: number[];
    let awsDevYs: number[];
    if (pCount >= aCount) {
      partnerYs = Array.from({ length: pCount }, (_, i) => groupCursorY + i * rowHeight);
      const anchorCenter = pCount > 0 ? groupCursorY + ((pCount - 1) * rowHeight) / 2 : groupCursorY;
      const targets = group.awsDevs.map((aws) => {
        const ys: number[] = [];
        for (const pid of awsToPartner.get(aws.id) ?? []) {
          const idx = group.partners.findIndex((p) => p.id === pid);
          if (idx >= 0) ys.push(partnerYs[idx]);
        }
        return ys.length > 0 ? ys.reduce((a, b) => a + b, 0) / ys.length : anchorCenter;
      });
      awsDevYs = packAlongTargets(targets);
    } else {
      awsDevYs = Array.from({ length: aCount }, (_, i) => groupCursorY + i * rowHeight);
      const anchorCenter = aCount > 0 ? groupCursorY + ((aCount - 1) * rowHeight) / 2 : groupCursorY;
      const targets = group.partners.map((p) => {
        const ys: number[] = [];
        for (const aid of partnerToAws.get(p.id) ?? []) {
          const idx = group.awsDevs.findIndex((a) => a.id === aid);
          if (idx >= 0) ys.push(awsDevYs[idx]);
        }
        return ys.length > 0 ? ys.reduce((a, b) => a + b, 0) / ys.length : anchorCenter;
      });
      partnerYs = packAlongTargets(targets);
    }

    for (let i = 0; i < pCount; i++) {
      const node = group.partners[i];
      const dim = nodeDim(node.data.category);
      const nodeX = partnerX + (partnerColW - dim.width) / 2;
      positioned.set(node.id, { ...node, position: { x: nodeX, y: partnerYs[i] } });
    }
    for (let i = 0; i < aCount; i++) {
      const node = group.awsDevs[i];
      const dim = nodeDim(node.data.category);
      const nodeX = awsDevX + (awsDevColW - dim.width) / 2;
      positioned.set(node.id, { ...node, position: { x: nodeX, y: awsDevYs[i] } });
    }

    let groupEndY = groupStartY + globalMaxH;
    for (const y of partnerYs) groupEndY = Math.max(groupEndY, y + globalMaxH);
    for (const y of awsDevYs) groupEndY = Math.max(groupEndY, y + globalMaxH);
    groupYRanges.push({ locCode: group.locCode, startY: groupStartY, endY: groupEndY });
    groupCursorY = groupEndY + locGroupGap;
  }

  const totalLocHeight = groupCursorY > 0 ? groupCursorY - locGroupGap : dxStartY;

  // ---- Step 5: Position DX onPremise nodes aligned to their location groups ----
  const dxOnPremNodes = existingLeaf.filter((n) => n.data.category === 'onPremise' && !isVpnNode(n));
  const recOnPremNodes = recommendedLeaf.filter((n) => n.data.category === 'onPremise' && !isVpnNode(n));
  const allDxOnPrems = [...dxOnPremNodes, ...recOnPremNodes];

  // Position DX onPremise nodes: center on their location group
  const onPremX = colX.get('onPremise') ?? 0;
  const onPremColW = colMaxWidth.get('onPremise') ?? 0;

  let unplacedOnPremCursorY = groupYRanges.length > 0
    ? groupYRanges[groupYRanges.length - 1].endY + locGroupGap
    : dxStartY;

  for (const onPrem of allDxOnPrems) {
    const dim = nodeDim(onPrem.data.category);
    const nodeX = onPremX + (onPremColW - dim.width) / 2;

    // Try to match to a location group. Real on-prem nodes pair with location
    // codes via `onprem-{locCode}`; ghost on-prem nodes carry per-DXGW IDs that
    // need separate resolution (see recOnpremLocCode).
    let matched = false;
    for (const gr of groupYRanges) {
      if (onPrem.id === `onprem-${gr.locCode}`) {
        const groupCenter = (gr.startY + gr.endY) / 2;
        const nodeY = groupCenter - dim.height / 2;
        positioned.set(onPrem.id, { ...onPrem, position: { x: nodeX, y: nodeY } });
        matched = true;
        break;
      }
    }
    if (!matched) {
      const recLocCode = recOnpremLocCode(onPrem.id);
      const recGr = recLocCode ? groupYRanges.find((r) => r.locCode === recLocCode) : undefined;
      if (recGr) {
        const groupCenter = (recGr.startY + recGr.endY) / 2;
        const nodeY = groupCenter - dim.height / 2;
        positioned.set(onPrem.id, { ...onPrem, position: { x: nodeX, y: nodeY } });
      } else {
        positioned.set(onPrem.id, { ...onPrem, position: { x: nodeX, y: unplacedOnPremCursorY } });
        unplacedOnPremCursorY += rowHeight;
      }
    }
  }

  // ---- Step 6: Position remaining columns (dxGateway, tgw_vgw, vpc) ----
  // Region-aware: group nodes by region and place each region's nodes as a vertical block.
  // This prevents region containers from overlapping in multi-region topologies.

  function getNodeRegion(n: DxNode): string {
    return (n.data.details as Record<string, string>)?.region ?? '_default';
  }

  const REGION_GROUP_GAP = CONTAINER_PAD_TOP + CONTAINER_PAD_BOTTOM + LOC_VISUAL_GAP;

  function positionColumnAtY(colKey: string, sortedNodes: DxNode[], startY: number, xOffset = 0) {
    const x = (colX.get(colKey) ?? 0) + xOffset;
    const colW = colMaxWidth.get(colKey) ?? 0;

    let curY = startY;
    for (const node of sortedNodes) {
      const dim = nodeDim(node.data.category, node);
      const nodeX = x + (colW - dim.width) / 2;
      positioned.set(node.id, { ...node, position: { x: nodeX, y: curY } });
      curY += dim.height + vGap;
    }
    return curY - vGap; // endY (bottom of last node)
  }

  // Barycenter sort: order nodes by average Y of their connected neighbours
  function barycenterSort(columnNodes: DxNode[]): DxNode[] {
    const scored = columnNodes.map((node) => {
      const ys: number[] = [];
      for (const e of edges) {
        if (e.target === node.id) {
          const src = positioned.get(e.source);
          if (src) ys.push(src.position.y + nodeDim(src.data.category).height / 2);
        }
        if (e.source === node.id) {
          const tgt = positioned.get(e.target);
          if (tgt) ys.push(tgt.position.y + nodeDim(tgt.data.category).height / 2);
        }
      }
      const avgY = ys.length > 0 ? ys.reduce((a, b) => a + b, 0) / ys.length : Infinity;
      return { node, avgY };
    });
    scored.sort((a, b) => a.avgY - b.avgY);
    return scored.map((s) => s.node);
  }

  // Generate all permutations (for small arrays ≤7)
  function permutations<T>(arr: T[]): T[][] {
    if (arr.length <= 1) return [arr];
    const result: T[][] = [];
    for (let i = 0; i < arr.length; i++) {
      const rest = [...arr.slice(0, i), ...arr.slice(i + 1)];
      for (const perm of permutations(rest)) {
        result.push([arr[i], ...perm]);
      }
    }
    return result;
  }

  // Count edge crossings between two ordered node lists
  function countCrossings(leftNodes: DxNode[], rightNodes: DxNode[]): number {
    const leftIds = leftNodes.map((n) => n.id);
    const rightIds = rightNodes.map((n) => n.id);
    const pairs: [number, number][] = [];
    for (const e of edges) {
      const li = leftIds.indexOf(e.source);
      const ri = rightIds.indexOf(e.target);
      if (li >= 0 && ri >= 0) { pairs.push([li, ri]); continue; }
      const li2 = leftIds.indexOf(e.target);
      const ri2 = rightIds.indexOf(e.source);
      if (li2 >= 0 && ri2 >= 0) { pairs.push([li2, ri2]); }
    }
    let crossings = 0;
    for (let i = 0; i < pairs.length; i++) {
      for (let j = i + 1; j < pairs.length; j++) {
        const [a1, b1] = pairs[i];
        const [a2, b2] = pairs[j];
        if ((a1 < a2 && b1 > b2) || (a1 > a2 && b1 < b2)) crossings++;
      }
    }
    return crossings;
  }

  // Optimize ordering of rightNodes to minimize crossings with leftNodes
  function optimizeOrder(leftNodes: DxNode[], rightNodes: DxNode[]): DxNode[] {
    if (rightNodes.length <= 1 || rightNodes.length > 7) return rightNodes;
    let bestOrder = rightNodes;
    let bestCrossings = countCrossings(leftNodes, rightNodes);
    for (const perm of permutations(rightNodes)) {
      const c = countCrossings(leftNodes, perm);
      if (c < bestCrossings) {
        bestCrossings = c;
        bestOrder = perm;
      }
    }
    return bestOrder;
  }

  // VPN connection ('cgw' column) now flows through the region layout so the
  // region container wraps it — keep it in rightCols.
  const rightCols = COLUMN_DEFS.filter(
    (c) => !['onPremise', 'dxPartnerDevice', 'awsDevice'].includes(c.key)
  );

  // Collect nodes per right column
  const colNodes = new Map<string, DxNode[]>();
  for (const col of rightCols) {
    const ex = existingLeaf.filter((n) => col.categories.includes(n.data.category));
    const rec = recommendedLeaf.filter((n) => col.categories.includes(n.data.category));
    colNodes.set(col.key, [...ex, ...rec]);
  }

  // Unique regions from tgw_vgw/vpc columns (dxGateway and coreNetwork are
  // region-agnostic). Order is strictly alphabetical — stable across node
  // visibility toggles (non-DX VPCs, group expand/collapse) and across
  // partial first-render states from streaming fetches.
  const globalCols = new Set(['dxGateway', 'coreNetwork']);
  const discoveredRegionSet = new Set<string>();
  for (const col of rightCols) {
    if (globalCols.has(col.key)) continue;
    for (const n of colNodes.get(col.key) ?? []) {
      const r = getNodeRegion(n);
      if (r !== '_default') discoveredRegionSet.add(r);
    }
  }
  const discoveredRegions = [...discoveredRegionSet].sort();
  const regionOrder: string[] = [...discoveredRegions];

  // For single-region (or no region), use legacy centered positioning
  if (regionOrder.length <= 1) {
    function positionColumnCentered(colKey: string, sortedNodes: DxNode[]) {
      const x = colX.get(colKey) ?? 0;
      const colW = colMaxWidth.get(colKey) ?? 0;
      let totalHeight = 0;
      for (const n of sortedNodes) totalHeight += nodeDim(n.data.category, n).height + vGap;
      totalHeight -= vGap;
      // Center on the DX-location band midpoint, not on [0, totalLocHeight]:
      // totalLocHeight measures from y=0, but the band's top sits at dxStartY
      // (pushed down by the VPN section). Using totalLocHeight/2 as the center
      // places the column half a dxStartY above the real midpoint.
      const bandMid = (dxStartY + totalLocHeight) / 2;
      const offsetY = bandMid - totalHeight / 2;
      let curY = Math.max(0, offsetY);
      for (const node of sortedNodes) {
        const dim = nodeDim(node.data.category, node);
        const nodeX = x + (colW - dim.width) / 2;
        positioned.set(node.id, { ...node, position: { x: nodeX, y: curY } });
        curY += dim.height + vGap;
      }
    }

    // Forward pass: position with barycenter sort, then optimize crossings between adjacent columns
    const colOrder: { key: string; nodes: DxNode[] }[] = [];
    let prevColNodes: DxNode[] = [];
    for (const col of rightCols) {
      const nodes = colNodes.get(col.key) ?? [];
      if (nodes.length === 0) continue;
      let sorted = barycenterSort(nodes);
      if (prevColNodes.length > 0) {
        sorted = optimizeOrder(prevColNodes, sorted);
      }
      positionColumnCentered(col.key, sorted);
      colOrder.push({ key: col.key, nodes: sorted });
      prevColNodes = sorted;
    }

    // Reverse pass: re-optimize each column against its right neighbour to reduce crossings
    for (let i = colOrder.length - 2; i >= 0; i--) {
      const reoptimized = optimizeOrder(colOrder[i + 1].nodes, colOrder[i].nodes);
      colOrder[i].nodes = reoptimized;
      positionColumnCentered(colOrder[i].key, reoptimized);
    }

  } else {
    // Multi-region: position each region's nodes as a separate vertical block.
    // First position global columns (dxGateway, coreNetwork) — not region-specific — centered on the DX-location band.
    // Center on the band midpoint (dxStartY → totalLocHeight) rather than on
    // [0, totalLocHeight]: the band's top sits at dxStartY when VPN pushes it
    // down, and the old formula left the column half a dxStartY too high.
    for (const globalColKey of globalCols) {
      const globalNodes = colNodes.get(globalColKey) ?? [];
      if (globalNodes.length === 0) continue;
      const sorted = barycenterSort(globalNodes);
      const totalHeight = sorted.length * rowHeight - vGap;
      const bandMid = (dxStartY + totalLocHeight) / 2;
      const offsetY = Math.max(dxStartY, bandMid - totalHeight / 2);
      const x = colX.get(globalColKey) ?? 0;
      const colW = colMaxWidth.get(globalColKey) ?? 0;
      for (let i = 0; i < sorted.length; i++) {
        const node = sorted[i];
        const dim = nodeDim(node.data.category);
        const nodeX = x + (colW - dim.width) / 2;
        const nodeY = offsetY + i * rowHeight;
        positioned.set(node.id, { ...node, position: { x: nodeX, y: nodeY } });
      }
    }

    // Now position tgw_vgw and vpc columns grouped by region
    // Connection-aware offset: only shift regions that have direct inter-region edges
    // (e.g. TGW peering) so those edges flow as smooth diagonals.
    const regionNodeIds = new Map<string, Set<string>>(); // region → node IDs in tgw_vgw/vpc
    for (const col of rightCols) {
      if (globalCols.has(col.key)) continue;
      for (const n of colNodes.get(col.key) ?? []) {
        const r = getNodeRegion(n);
        if (r === '_default') continue;
        const s = regionNodeIds.get(r) ?? new Set();
        s.add(n.id);
        regionNodeIds.set(r, s);
      }
    }

    let regionCursorY = dxStartY;
    const positionedRegions = new Set<string>(); // regions already laid out

    // Region-aware barycenter: VPCs connected to global columns (dxGateway/coreNetwork)
    // or nodes in a different region cluster on the side of the region closest to
    // neighbouring regions — so cross-region edges stay short and in-region TGW
    // clusters stay tight. All non-last regions push global neighbours DOWN; the last
    // region pushes UP so its global-connected nodes sit closest to the region above.
    const BIG_OFFSET = 100000;
    function regionAwareBarycenterSort(columnNodes: DxNode[], currentRegion: string): DxNode[] {
      const regionIdx = regionOrder.indexOf(currentRegion);
      const isLastRegion = regionIdx === regionOrder.length - 1;
      const scored = columnNodes.map((node) => {
        const ys: number[] = [];
        for (const e of edges) {
          const otherId = e.source === node.id ? e.target : e.target === node.id ? e.source : null;
          if (!otherId) continue;
          const other = positioned.get(otherId);
          if (other) {
            const otherRegion = getNodeRegion(other);
            const otherCol = COLUMN_DEFS.find((c) => c.categories.includes(other.data.category));
            const isGlobal = otherCol ? globalCols.has(otherCol.key) : false;
            const isDifferentRegion = otherRegion !== '_default' && otherRegion !== currentRegion;
            const nY = other.position.y + nodeDim(other.data.category).height / 2;
            if (isGlobal || isDifferentRegion) {
              ys.push(isLastRegion ? nY - BIG_OFFSET : nY + BIG_OFFSET);
            } else {
              ys.push(nY);
            }
          } else {
            // Peer not yet positioned (e.g. TGW peering into a later region).
            // Look it up via regionNodeIds so cross-region edges still
            // influence sort — without this, peering TGWs get placed by their
            // in-region attachments only and the peering edge has to cut
            // through the entire region container.
            let peerRegion: string | undefined;
            for (const [r, ids] of regionNodeIds) {
              if (ids.has(otherId)) { peerRegion = r; break; }
            }
            if (!peerRegion || peerRegion === currentRegion) continue;
            const peerIdx = regionOrder.indexOf(peerRegion);
            // peer above → push node up (toward top, closest to peer);
            // peer below → push node down (toward bottom, closest to peer).
            ys.push(peerIdx < regionIdx ? -BIG_OFFSET : BIG_OFFSET);
          }
        }
        const avgY = ys.length > 0 ? ys.reduce((a, b) => a + b, 0) / ys.length : Infinity;
        return { node, avgY };
      });
      scored.sort((a, b) => a.avgY - b.avgY);
      return scored.map((s) => s.node);
    }

    for (const regionCode of regionOrder) {
      const regionStartY = regionCursorY;
      let regionEndY = regionCursorY;
      let prevRegionColNodes: DxNode[] = [];

      // Forward pass: left to right
      const regionColOrder: { key: string; nodes: DxNode[] }[] = [];
      for (const col of rightCols) {
        if (globalCols.has(col.key)) continue; // already positioned globally
        const allColNodes = colNodes.get(col.key) ?? [];
        const regionNodes = allColNodes.filter((n) => getNodeRegion(n) === regionCode);
        if (regionNodes.length === 0) continue;

        let sorted = regionAwareBarycenterSort(regionNodes, regionCode);
        // Optimize order to minimize edge crossings with previous column
        if (prevRegionColNodes.length > 0) {
          sorted = optimizeOrder(prevRegionColNodes, sorted);
        }
        const endY = positionColumnAtY(col.key, sorted, regionStartY);
        regionColOrder.push({ key: col.key, nodes: sorted });
        prevRegionColNodes = sorted;
        regionEndY = Math.max(regionEndY, endY);
      }

      // Reverse pass: re-optimize each column against its right neighbour
      for (let i = regionColOrder.length - 2; i >= 0; i--) {
        const reoptimized = optimizeOrder(regionColOrder[i + 1].nodes, regionColOrder[i].nodes);
        regionColOrder[i].nodes = reoptimized;
        const endY = positionColumnAtY(regionColOrder[i].key, reoptimized, regionStartY);
        regionEndY = Math.max(regionEndY, endY);
      }

      positionedRegions.add(regionCode);
      regionCursorY = regionEndY + REGION_GROUP_GAP;
    }

    // Re-position global column nodes (dxGateway, coreNetwork) at the barycenter
    // of their connected nodes so each aligns with its actual connections:
    // - DX Gateway: 1 left peer → snap to that row; multiple → center between them.
    // - Cloud WAN aligns between the regions it connects to (all connections).
    const leftSideCats = new Set(['awsDevice', 'dxPartnerDevice']);
    for (const globalColKey of globalCols) {
      const globalNodes = [...positioned.values()].filter(
        (n) => n.data.category === globalColKey
      );
      if (globalNodes.length === 0) continue;

      for (const node of globalNodes) {
        // For dxGateway, prefer left-side connections (VIFs/awsDevices) so edges
        // from the left flow horizontally. Unattached DXGWs have no left-side
        // neighbors — fall back to right-side connections so they sit next to
        // whatever they *do* connect to (e.g. a TGW on the DXGW-attachment path).
        const isDxGateway = node.data.category === 'dxGateway';
        const leftYs: number[] = [];
        const allYs: number[] = [];
        for (const e of edges) {
          const otherId = e.source === node.id ? e.target : e.target === node.id ? e.source : null;
          if (!otherId) continue;
          const otherNode = positioned.get(otherId);
          if (!otherNode) continue;
          const dim = nodeDim(otherNode.data.category);
          const centerY = otherNode.position.y + dim.height / 2;
          allYs.push(centerY);
          if (leftSideCats.has(otherNode.data.category)) leftYs.push(centerY);
        }
        const connectedYs = isDxGateway && leftYs.length > 0 ? leftYs : allYs;
        if (connectedYs.length > 0) {
          const avgY = connectedYs.reduce((a, b) => a + b, 0) / connectedYs.length;
          const dim = nodeDim(node.data.category);
          const nodeY = Math.max(dxStartY, avgY - dim.height / 2);
          positioned.set(node.id, { ...node, position: { x: node.position.x, y: nodeY } });
        }
      }

      // Collision avoidance: two global-column nodes can end up with the same or
      // overlapping barycenter Y (e.g. two DXGWs sharing the same awsDevices).
      // Sort by current Y and push later nodes down so each gets at least `vGap`
      // of clear space below the previous one.
      const sortedGlobal = [...positioned.values()]
        .filter((n) => n.data.category === globalColKey)
        .sort((a, b) => a.position.y - b.position.y);
      for (let i = 1; i < sortedGlobal.length; i++) {
        const prev = sortedGlobal[i - 1];
        const curr = sortedGlobal[i];
        const prevDim = nodeDim(prev.data.category);
        const minY = prev.position.y + prevDim.height + vGap;
        if (curr.position.y < minY) {
          positioned.set(curr.id, { ...curr, position: { x: curr.position.x, y: minY } });
          sortedGlobal[i] = { ...curr, position: { x: curr.position.x, y: minY } };
        }
      }
    }
  }

  // ---- Step 6b: Region-internal bipartite pairing layout ----
  //
  // Within each region, gateways (TGW/VGW) and VPCs form bipartite connected
  // components via direct attachment edges. Each component is placed by shape:
  //   - 1:1    → gateway and VPC share a Y (side by side)
  //   - 1:N    → gateway centered vertically on the VPC stack
  //   - N:1    → VPC centered vertically on the gateway stack
  //   - M:N    → shorter side centered on the taller side
  //   - orphan → gateway-only component stacks alone (e.g. TGW peered with a
  //              DXGW but no in-region VPC — zero-attachment TGWs route to
  //              the unattached zone before layout, so they don't reach here)
  //
  // Components stack top-down in each region, ordered by current-Y average —
  // this preserves the crossing-minimized order Step 6's barycenter pass
  // established from DXGW/awsDevice positions. The whole region block then
  // shifts so its mid-Y lands on the barycenter of EXTERNAL connections
  // (DXGW, CoreNetwork, cross-region peers), keeping DXGW↔TGW edges level
  // and inter-region peering edges short.
  //
  // tgwConnect nodes pin to their TGW's Y after pairing so the attachment
  // edge is a clean horizontal line.
  const gatewayCats = new Set(['tgw', 'vgw', 'tgwGroup', 'isolatedTgwGroup']);
  const vpcCats = new Set(['vpc', 'vpcGroup']);

  const regionsWithNodes = new Set<string>();
  for (const n of positioned.values()) {
    const r = getNodeRegion(n);
    if (r === '_default') continue;
    if (
      gatewayCats.has(n.data.category) ||
      vpcCats.has(n.data.category) ||
      n.data.category === 'tgwConnect'
    ) {
      regionsWithNodes.add(r);
    }
  }

  interface PairComponent {
    gateways: DxNode[];
    vpcs: DxNode[];
    gatewayLocalYs: number[];
    vpcLocalYs: number[];
    height: number;
    orderY: number;
    externalBarycenterY: number | null;
  }

  function stackHeightWithGap(ns: DxNode[]): number {
    if (ns.length === 0) return 0;
    let h = 0;
    for (let i = 0; i < ns.length; i++) {
      h += nodeDim(ns[i].data.category, ns[i]).height;
      if (i < ns.length - 1) h += vGap;
    }
    return h;
  }

  for (const regionKey of regionsWithNodes) {
    const gateways = [...positioned.values()].filter(
      (n) => gatewayCats.has(n.data.category) && getNodeRegion(n) === regionKey,
    );
    const vpcs = [...positioned.values()].filter(
      (n) => vpcCats.has(n.data.category) && getNodeRegion(n) === regionKey,
    );

    if (gateways.length > 0 || vpcs.length > 0) {
      // Bipartite adjacency: ONLY gateway↔VPC edges within this region.
      // TGW peering (gateway↔gateway) and DXGW/CoreNetwork edges are excluded
      // so components split cleanly on attachment boundaries — peered TGWs
      // that own different VPCs end up in different components and stack
      // vertically rather than getting fused into one giant component.
      const gwIds = new Set(gateways.map((n) => n.id));
      const vpcIds = new Set(vpcs.map((n) => n.id));
      const adj = new Map<string, string[]>();
      for (const n of [...gateways, ...vpcs]) adj.set(n.id, []);
      for (const e of edges) {
        const srcGw = gwIds.has(e.source);
        const tgtGw = gwIds.has(e.target);
        const srcVpc = vpcIds.has(e.source);
        const tgtVpc = vpcIds.has(e.target);
        if ((srcGw && tgtVpc) || (srcVpc && tgtGw)) {
          adj.get(e.source)!.push(e.target);
          adj.get(e.target)!.push(e.source);
        }
      }

      const visited = new Set<string>();
      const components: PairComponent[] = [];
      for (const seed of [...gateways, ...vpcs]) {
        if (visited.has(seed.id)) continue;
        visited.add(seed.id);
        const queue = [seed.id];
        const gws: DxNode[] = [];
        const vs: DxNode[] = [];
        while (queue.length > 0) {
          const id = queue.shift()!;
          const n = positioned.get(id)!;
          if (gwIds.has(id)) gws.push(n);
          else vs.push(n);
          for (const nb of adj.get(id) ?? []) {
            if (!visited.has(nb)) {
              visited.add(nb);
              queue.push(nb);
            }
          }
        }

        // Within-component order by current Y — Step 6's barycenter pass has
        // already minimized crossings from that ordering, so re-sorting by it
        // preserves that work.
        gws.sort((a, b) => a.position.y - b.position.y);
        vs.sort((a, b) => a.position.y - b.position.y);

        const gwH = stackHeightWithGap(gws);
        const vpcH = stackHeightWithGap(vs);
        const maxH = Math.max(gwH, vpcH);

        // Equal sides → gwStart == vpcStart == 0 (1:1 lines up at same Y).
        // Unequal → shorter side centers on taller (1:N puts gateway at the
        // midline of the VPC stack; N:1 puts VPC at the midline of gateways).
        const gwStart = (maxH - gwH) / 2;
        const vpcStart = (maxH - vpcH) / 2;

        const gatewayLocalYs: number[] = [];
        let cur = gwStart;
        for (const gw of gws) {
          gatewayLocalYs.push(cur);
          cur += nodeDim(gw.data.category, gw).height + vGap;
        }
        const vpcLocalYs: number[] = [];
        cur = vpcStart;
        for (const v of vs) {
          vpcLocalYs.push(cur);
          cur += nodeDim(v.data.category, v).height + vGap;
        }

        const currentCenters: number[] = [];
        for (const n of [...gws, ...vs]) {
          currentCenters.push(n.position.y + nodeDim(n.data.category, n).height / 2);
        }
        const orderY = currentCenters.length > 0
          ? currentCenters.reduce((a, b) => a + b, 0) / currentCenters.length
          : 0;

        // External barycenter: mean Y of edges leaving this component — to
        // DXGW, CoreNetwork, cross-region peers, or other same-region
        // components (TGW peering). Drives the region anchor so DXGW↔TGW
        // edges land level. tgwConnect is skipped — its Y is set AFTER
        // pairing, so the current value is stale.
        const inComp = new Set([...gws, ...vs].map((n) => n.id));
        const extYs: number[] = [];
        for (const n of [...gws, ...vs]) {
          for (const e of edges) {
            const otherId = e.source === n.id ? e.target : e.target === n.id ? e.source : null;
            if (!otherId || inComp.has(otherId)) continue;
            const other = positioned.get(otherId);
            if (!other) continue;
            if (other.data.category === 'tgwConnect') continue;
            const oDim = nodeDim(other.data.category, other);
            extYs.push(other.position.y + oDim.height / 2);
          }
        }
        const externalBarycenterY = extYs.length > 0
          ? extYs.reduce((a, b) => a + b, 0) / extYs.length
          : null;

        components.push({
          gateways: gws,
          vpcs: vs,
          gatewayLocalYs,
          vpcLocalYs,
          height: maxH,
          orderY,
          externalBarycenterY,
        });
      }

      if (components.length > 0) {
        // Identify gateways that terminate a VPN Connection in this region.
        // The component containing such a gateway must stack at the TOP of
        // the column: the VPN Connection node (cgw) is later placed directly
        // above the topmost gateway (see the per-region cgw pin pass below),
        // and if a non-peer component sits above the peer's, the vertical
        // tunnel edge drops through an unrelated gateway and its "VPN
        // Tunnel" label lands on that gateway.
        const vpnPeerGwIds = new Set<string>();
        for (const e of edges) {
          const srcNode = positioned.get(e.source);
          const tgtNode = positioned.get(e.target);
          if (srcNode?.data.category === 'cgw' && getNodeRegion(srcNode) === regionKey && gwIds.has(e.target)) {
            vpnPeerGwIds.add(e.target);
          } else if (tgtNode?.data.category === 'cgw' && getNodeRegion(tgtNode) === regionKey && gwIds.has(e.source)) {
            vpnPeerGwIds.add(e.source);
          }
        }
        const hasVpnPeer = (c: PairComponent) => c.gateways.some((g) => vpnPeerGwIds.has(g.id));
        components.sort((a, b) => {
          const aVpn = hasVpnPeer(a) ? 0 : 1;
          const bVpn = hasVpnPeer(b) ? 0 : 1;
          if (aVpn !== bVpn) return aVpn - bVpn;
          return a.orderY - b.orderY;
        });

        // Within the VPN-peer component, ensure the actual peer gateway is
        // the topmost one — matters when the component has multiple gateways
        // (e.g. peered TGWs) and only one terminates the VPN. Recompute
        // gatewayLocalYs after reorder so the stack lines up.
        for (const c of components) {
          if (!hasVpnPeer(c)) continue;
          c.gateways.sort((a, b) => {
            const aPeer = vpnPeerGwIds.has(a.id) ? 0 : 1;
            const bPeer = vpnPeerGwIds.has(b.id) ? 0 : 1;
            if (aPeer !== bPeer) return aPeer - bPeer;
            return a.position.y - b.position.y;
          });
          let cur = (c.height - stackHeightWithGap(c.gateways)) / 2;
          for (let i = 0; i < c.gateways.length; i++) {
            c.gatewayLocalYs[i] = cur;
            cur += nodeDim(c.gateways[i].data.category, c.gateways[i]).height + vGap;
          }
        }

        const extBary = components
          .map((c) => c.externalBarycenterY)
          .filter((v): v is number => v != null);
        const totalHeight = components.reduce(
          (s, c, i) => s + c.height + (i > 0 ? vGap : 0),
          0,
        );
        // Pure-orphan region (no external edges anywhere) — keep the current
        // mean Y so the block doesn't jump.
        const regionMid = extBary.length > 0
          ? extBary.reduce((a, b) => a + b, 0) / extBary.length
          : components.reduce((s, c) => s + c.orderY, 0) / components.length;
        const regionTopY = regionMid - totalHeight / 2;

        let compY = regionTopY;
        for (const comp of components) {
          for (let i = 0; i < comp.gateways.length; i++) {
            const gw = comp.gateways[i];
            const current = positioned.get(gw.id)!;
            positioned.set(gw.id, {
              ...current,
              position: { x: current.position.x, y: compY + comp.gatewayLocalYs[i] },
            });
          }
          for (let i = 0; i < comp.vpcs.length; i++) {
            const v = comp.vpcs[i];
            const current = positioned.get(v.id)!;
            positioned.set(v.id, {
              ...current,
              position: { x: current.position.x, y: compY + comp.vpcLocalYs[i] },
            });
          }
          compY += comp.height + vGap;
        }
      }
    }

    // tgwConnect: pin each to its TGW neighbour's Y so the attachment edge is
    // horizontal. Runs AFTER gateways settle in the pairing pass above.
    const tgwConnects = [...positioned.values()].filter(
      (n) => n.data.category === 'tgwConnect' && getNodeRegion(n) === regionKey,
    );
    for (const tc of tgwConnects) {
      const tgwYs: number[] = [];
      for (const e of edges) {
        const otherId = e.source === tc.id ? e.target : e.target === tc.id ? e.source : null;
        if (!otherId) continue;
        const other = positioned.get(otherId);
        if (!other || !gatewayCats.has(other.data.category)) continue;
        tgwYs.push(other.position.y + nodeDim(other.data.category, other).height / 2);
      }
      if (tgwYs.length > 0) {
        const avg = tgwYs.reduce((a, b) => a + b, 0) / tgwYs.length;
        const dim = nodeDim(tc.data.category, tc);
        const current = positioned.get(tc.id)!;
        positioned.set(tc.id, {
          ...current,
          position: { x: current.position.x, y: avg - dim.height / 2 },
        });
      }
    }

    // VPN connections (cgw): all VPNs in the region form a single HORIZONTAL
    // ROW at the top of the region, above the highest gateway. Each VPN sits
    // centered on its own gateway's X where possible; when two VPNs' default
    // X would overlap, the later one slides right by node width + vGap so
    // tunnel labels don't collide. Stacking them vertically (the old
    // behavior) produced tunnel edges from upper VPNs that cut through lower
    // VPNs' bodies and dropped labels on the wrong node.
    const vpnConnNodes = [...positioned.values()].filter(
      (n) => n.data.category === 'cgw' && getNodeRegion(n) === regionKey,
    );
    const vpnTopPeer = new Map<string, DxNode>();
    const vpnWithPeer: DxNode[] = [];
    for (const vc of vpnConnNodes) {
      const gwPeers: DxNode[] = [];
      for (const e of edges) {
        const otherId = e.source === vc.id ? e.target : e.target === vc.id ? e.source : null;
        if (!otherId) continue;
        const other = positioned.get(otherId);
        if (!other || !gatewayCats.has(other.data.category)) continue;
        gwPeers.push(other);
      }
      if (gwPeers.length === 0) continue;
      const topPeer = gwPeers.reduce((top, g) => (g.position.y < top.position.y ? g : top), gwPeers[0]);
      vpnTopPeer.set(vc.id, topPeer);
      vpnWithPeer.push(vc);
    }

    if (vpnWithPeer.length > 0) {
      // One common Y for the whole row — anchored above the highest peer so
      // every tunnel edge has room for its "VPN Tunnel\n<name>" label plus
      // tunnel-status rows (live mode). 3× vGap matches the per-peer spacing
      // used before the horizontal-row rework.
      const vcHeightMax = vpnWithPeer.reduce(
        (m, vc) => Math.max(m, nodeDim(vc.data.category, vc).height),
        0,
      );
      const highestPeerY = vpnWithPeer.reduce(
        (minY, vc) => Math.min(minY, vpnTopPeer.get(vc.id)!.position.y),
        Infinity,
      );
      const rowY = highestPeerY - vGap * 3 - vcHeightMax;

      // Sort by peer X so the VPN row mirrors the gateway column order below.
      // Stable tiebreak on VPN id keeps positions deterministic across reloads.
      vpnWithPeer.sort((a, b) => {
        const ax = vpnTopPeer.get(a.id)!.position.x;
        const bx = vpnTopPeer.get(b.id)!.position.x;
        return ax !== bx ? ax - bx : a.id.localeCompare(b.id);
      });

      // Place each VPN at its peer's centered X; push right whenever it
      // would overlap the previous VPN's footprint.
      let prevRight = -Infinity;
      for (const vc of vpnWithPeer) {
        const peer = vpnTopPeer.get(vc.id)!;
        const peerDim = nodeDim(peer.data.category, peer);
        const vcDim = nodeDim(vc.data.category, vc);
        const centeredX = peer.position.x + (peerDim.width - vcDim.width) / 2;
        const x = Math.max(centeredX, prevRight + vGap);
        const current = positioned.get(vc.id)!;
        positioned.set(vc.id, {
          ...current,
          position: { x, y: rowY },
        });
        prevRight = x + vcDim.width;
      }
    }
  }

  // NOTE: no global column overlap-resolution here — Step 6b already tight-packs
  // each region's columns and Step 6e repacks regions to REGION_GROUP_GAP.
  // A global push-down sort would cascade overlaps BETWEEN regions INTO regions,
  // undoing the tight pack.

  // ---- Step 6e: Repack regions to exact REGION_GROUP_GAP ----
  // After barycenter recentering (6b-6d), regions drift: some pulled up by global-column
  // edges (DXGW), others pushed down by VPC fan-out. This leaves arbitrary gaps or overlaps
  // between region containers. Sort regions by their post-recenter top edge, then pack them
  // top-down at exactly REGION_GROUP_GAP apart — preserving internal ordering from Step 6
  // while eliminating both gaps and overlaps.
  if (regionOrder.length > 1) {
    const regionBoundsCats = new Set(['tgw', 'tgwGroup', 'isolatedTgwGroup', 'tgwConnect', 'vgw', 'vpc', 'vpcGroup', 'cgw']);

    const regionNodeIds3 = new Map<string, string[]>();
    for (const [id, node] of positioned) {
      if (!regionBoundsCats.has(node.data.category)) continue;
      const r = getNodeRegion(node);
      if (r === '_default') continue;
      const arr = regionNodeIds3.get(r) ?? [];
      arr.push(id);
      regionNodeIds3.set(r, arr);
    }

    const regionBounds: { region: string; minY: number; maxY: number; nodeIds: string[] }[] = [];
    for (const region of regionOrder) {
      const ids = regionNodeIds3.get(region);
      if (!ids || ids.length === 0) continue;
      let minY = Infinity, maxY = -Infinity;
      for (const id of ids) {
        const n = positioned.get(id)!;
        const dim = nodeDim(n.data.category, n);
        minY = Math.min(minY, n.position.y);
        maxY = Math.max(maxY, n.position.y + dim.height);
      }
      regionBounds.push({ region, minY, maxY, nodeIds: ids });
    }

    // `regionBounds` stays in `regionOrder` iteration order — do not sort by
    // minY. Post-barycenter Y depends on which VPCs render, so a minY sort
    // would reorder regions whenever the non-DX visibility toggle fires.

    // Anchor Y is unimportant — Step 9 shifts the whole AWS Cloud content block
    // so its minY lands at the container's inner top. What matters here is the
    // RELATIVE ordering and spacing of regions, which this loop preserves.
    let packCursorY = regionBounds.length > 0 ? regionBounds[0].minY : dxStartY;
    for (const rb of regionBounds) {
      const shift = packCursorY - rb.minY;
      if (shift !== 0) {
        for (const id of rb.nodeIds) {
          const node = positioned.get(id)!;
          positioned.set(id, { ...node, position: { x: node.position.x, y: node.position.y + shift } });
        }
        rb.minY += shift;
        rb.maxY += shift;
      }
      packCursorY = rb.maxY + REGION_GROUP_GAP;
    }
  }

  // Step 6f (removed): previously shifted the entire DXGW column as a block to
  // center on the DX-location band midpoint. That snapped every DXGW onto the
  // same relative offset regardless of which awsDevice it actually connected to,
  // so a DXGW with a single 1-to-1 peer would drift off its peer's row. The
  // Step 6 barycenter pass already reads post-VPN awsDevice Ys, so no
  // column-wide realignment is needed.

  // ---- Step 6g: Re-center Core Network on its final connection positions ----
  // When CoreNetwork is directly connected to a DX Gateway, align on the DXGW's
  // Y only so the DXGW → CoreNetwork edge is a clean horizontal line — averaging
  // all TGW/VPC connections drifts CoreNetwork away from the DXGW and produces a
  // visible "stair" between them. Pure Cloud WAN (no DXGW) falls back to the
  // all-connections barycenter so it still sits between its TGWs/VPCs. Runs after
  // Step 6f so the final DXGW Y is used as the alignment reference.
  {
    const coreNetworkNodes = [...positioned.values()].filter((n) => n.data.category === 'coreNetwork');
    for (const node of coreNetworkNodes) {
      const dxgwYs: number[] = [];
      const fallbackYs: number[] = [];
      for (const e of edges) {
        const otherId = e.source === node.id ? e.target : e.target === node.id ? e.source : null;
        if (!otherId) continue;
        const other = positioned.get(otherId);
        if (!other) continue;
        const oDim = nodeDim(other.data.category, other);
        const centerY = other.position.y + oDim.height / 2;
        fallbackYs.push(centerY);
        if (other.data.category === 'dxGateway') dxgwYs.push(centerY);
      }
      const sourceYs = dxgwYs.length > 0 ? dxgwYs : fallbackYs;
      if (sourceYs.length === 0) continue;
      const avgY = sourceYs.reduce((a, b) => a + b, 0) / sourceYs.length;
      const dim = nodeDim(node.data.category);
      const nodeY = Math.max(dxStartY, avgY - dim.height / 2);
      positioned.set(node.id, { ...node, position: { x: node.position.x, y: nodeY } });
    }
  }

  // ---- Step 7: Position DX Location containers ----
  const childrenByLoc = new Map<string, DxNode[]>();
  for (const n of positioned.values()) {
    if (
      n.data.category === 'dxPartnerDevice' ||
      n.data.category === 'dxPartnerDeviceGroup' ||
      n.data.category === 'awsDevice'
    ) {
      const lc = getLocCode(n);
      if (lc) {
        const arr = childrenByLoc.get(lc) ?? [];
        arr.push(n);
        childrenByLoc.set(lc, arr);
      }
    }
  }

  for (const loc of dxLocContainers.filter((n) => !n.data.isRecommended)) {
    const locCode = getContainerCode(loc);
    const children = childrenByLoc.get(locCode) ?? [];

    if (children.length === 0) {
      const pX = colX.get('dxPartnerDevice') ?? 0;
      const aX = colX.get('awsDevice') ?? 0;
      const aW = colMaxWidth.get('awsDevice') ?? 80;
      const w = aX + aW - pX + CONTAINER_PAD_X * 2;
      const h = globalMaxH + CONTAINER_PAD_TOP + CONTAINER_PAD_BOTTOM;
      setContainer(positioned, loc, pX - CONTAINER_PAD_X, dxStartY - CONTAINER_PAD_TOP, w, h);
      continue;
    }

    const bb = boundingBox(children);
    const w = bb.maxX - bb.minX + CONTAINER_PAD_X * 2;
    const h = bb.maxY - bb.minY + CONTAINER_PAD_TOP + CONTAINER_PAD_BOTTOM;
    setContainer(positioned, loc, bb.minX - CONTAINER_PAD_X, bb.minY - CONTAINER_PAD_TOP, w, h);
  }

  for (const loc of dxLocContainers.filter((n) => n.data.isRecommended)) {
    const locCode = getContainerCode(loc);
    const children = childrenByLoc.get(locCode) ?? [];

    if (children.length === 0) {
      const pX = colX.get('dxPartnerDevice') ?? 0;
      const aX = colX.get('awsDevice') ?? 0;
      const aW = colMaxWidth.get('awsDevice') ?? 80;
      const w = aX + aW - pX + CONTAINER_PAD_X * 2;
      const h = globalMaxH + CONTAINER_PAD_TOP + CONTAINER_PAD_BOTTOM;
      const lastGroupEnd = groupYRanges.length > 0 ? groupYRanges[groupYRanges.length - 1].endY : dxStartY;
      setContainer(positioned, loc, pX - CONTAINER_PAD_X, lastGroupEnd + locGroupGap - CONTAINER_PAD_TOP, w, h);
    } else {
      const bb = boundingBox(children);
      const w = bb.maxX - bb.minX + CONTAINER_PAD_X * 2;
      const h = bb.maxY - bb.minY + CONTAINER_PAD_TOP + CONTAINER_PAD_BOTTOM;
      setContainer(positioned, loc, bb.minX - CONTAINER_PAD_X, bb.minY - CONTAINER_PAD_TOP, w, h);
    }
  }

  // ---- Step 7b: Position Customer Site containers (wraps onPremise/CGW nodes) ----
  const customerSiteContainers = nodes.filter((n) => n.data.category === 'customerSite');

  // VPN on-prem nodes can opt into an existing DX customer-site via
  // details.hostSiteId (set by topology-builder when a DX site exists).
  // This groups both CGWs into one "Customer Data Center" zone instead of
  // two stacked containers. Pre-position hosted VPN routers directly above
  // their host site's DX onPremise child so the zone grows to fit both.
  const hostedVpnByHostId = new Map<string, DxNode[]>();
  for (const n of positioned.values()) {
    if (!n.id.startsWith('onprem-vpn-')) continue;
    const hostId = (n.data.details as Record<string, string> | undefined)?.hostSiteId;
    if (!hostId) continue;
    const arr = hostedVpnByHostId.get(hostId) ?? [];
    arr.push(n);
    hostedVpnByHostId.set(hostId, arr);
  }
  for (const [hostSiteId, vpnRouters] of hostedVpnByHostId) {
    const companionId = siteCompanionOnpremId(hostSiteId);
    const host = companionId ? positioned.get(companionId) : undefined;
    if (!host) continue;
    const hostDim = nodeDim(host.data.category, host);
    let cursorY = host.position.y - vGap - nodeDim('onPremise').height;
    for (const vpn of vpnRouters) {
      const dim = nodeDim(vpn.data.category, vpn);
      positioned.set(vpn.id, {
        ...vpn,
        position: {
          x: host.position.x + (hostDim.width - dim.width) / 2,
          y: cursorY,
        },
      });
      cursorY -= dim.height + vGap;
    }
  }

  for (const site of customerSiteContainers) {
    // Pair a customer-site container with its onPremise child. Legacy ghost
    // pairs use rec-custsite-{X}/rec-onprem-{X}; per-DXGW ghosts use
    // rec-{dxgwId}-custsite-B/rec-{dxgwId}-onprem-B; real sites use
    // custsite-{loc}/onprem-{loc}; pure-VPN sites use
    // custsite-vpn-{cgw}/onprem-vpn-{cgw}. See siteCompanionOnpremId.
    const companion = siteCompanionOnpremId(site.id);
    const siteChildren = companion
      ? [...positioned.values()].filter(
          (n) => n.data.category === 'onPremise' && n.id === companion,
        )
      : [];
    // Include any VPN routers hosted inside this DX site — their positions
    // were updated above so the zone grows to fit them.
    const hostedVpns = hostedVpnByHostId.get(site.id) ?? [];
    const allChildren = [...siteChildren, ...hostedVpns];

    const sizeOverride = nodeSizeOverrides?.get(site.id);

    if (allChildren.length === 0) {
      // Fallback: place at onPremise column position
      const opX = colX.get('onPremise') ?? 0;
      const opW = colMaxWidth.get('onPremise') ?? 200;
      const w = sizeOverride?.width ?? (opW + CONTAINER_PAD_X * 2);
      const h = sizeOverride?.height ?? (globalMaxH + CONTAINER_PAD_TOP + CONTAINER_PAD_BOTTOM);
      setContainer(positioned, site, opX - CONTAINER_PAD_X, dxStartY - CONTAINER_PAD_TOP, w, h);
      continue;
    }

    const bb = boundingBox(allChildren);
    const w = sizeOverride?.width ?? (bb.maxX - bb.minX + CONTAINER_PAD_X * 2);
    const h = sizeOverride?.height ?? (bb.maxY - bb.minY + CONTAINER_PAD_TOP + CONTAINER_PAD_BOTTOM);
    setContainer(positioned, site, bb.minX - CONTAINER_PAD_X, bb.minY - CONTAINER_PAD_TOP, w, h);
  }

  // ---- Step 8: Position Region containers ----
  const emptyRegionIds = new Set<string>();
  // First pass: compute each region's natural bounding box and the max width
  // across regions that will render the "Show/Hide non-DX VPCs" toggle. Those
  // regions share the widest one so the header never squashes the label/button
  // onto multiple lines in a narrow region (e.g. one with a single TGW column).
  interface RegionBox {
    region: DxNode;
    bb: { minX: number; minY: number; maxX: number; maxY: number };
    naturalW: number;
    hasToggle: boolean;
  }
  const regionBoxes: RegionBox[] = [];
  let maxToggleRegionW = 0;
  for (const region of regionContainers) {
    const regionCode = (region.data.details as Record<string, string>)?.regionCode ?? '';
    const regionChildren = [...positioned.values()].filter((n) => {
      if (!['coreNetwork', 'tgw', 'tgwGroup', 'isolatedTgwGroup', 'vgw', 'vpc', 'vpcGroup', 'cgw'].includes(n.data.category)) return false;
      if (n.data.isRecommended) return false;
      const nodeRegion = (n.data.details as Record<string, string>)?.region;
      if (nodeRegion) return nodeRegion === regionCode;
      return n.id.includes(regionCode);
    });

    if (regionChildren.length === 0) {
      emptyRegionIds.add(region.id);
      continue;
    }

    const bb = boundingBox(regionChildren);
    const naturalW = bb.maxX - bb.minX + CONTAINER_PAD_X * 2;
    const hasToggle = (region.data.nonDxVpcCount ?? 0) > 0;
    if (hasToggle && naturalW > maxToggleRegionW) maxToggleRegionW = naturalW;
    regionBoxes.push({ region, bb, naturalW, hasToggle });
  }

  for (const { region, bb, naturalW, hasToggle } of regionBoxes) {
    const w = hasToggle ? Math.max(naturalW, maxToggleRegionW) : naturalW;
    const h = bb.maxY - bb.minY + CONTAINER_PAD_TOP + CONTAINER_PAD_BOTTOM;
    setContainer(positioned, region, bb.minX - CONTAINER_PAD_X, bb.minY - CONTAINER_PAD_TOP, w, h);
  }

  // ---- Step 9: Position AWS Cloud container (wraps DX Gateways + Regions) ----
  const awsCloudNode = nodes.find((n) => n.data.category === 'awsCloud');
  if (awsCloudNode) {
    // Collect all nodes that should be inside the AWS Cloud: DX Gateways, TGW/VGW, VPC, VpcGroup, and Region containers
    const awsChildren = [...positioned.values()].filter(
      (n) => ['dxGateway', 'coreNetwork', 'tgw', 'tgwGroup', 'isolatedTgwGroup', 'vgw', 'vpc', 'vpcGroup', 'region', 'cgw', 'tgwConnect'].includes(n.data.category)
    );

    if (awsChildren.length > 0) {
      // For region containers, use their full width/height
      const AWS_CLOUD_PAD_X = 45;
      const AWS_CLOUD_PAD_TOP = 55;
      const AWS_CLOUD_PAD_BOTTOM = 35;

      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const n of awsChildren) {
        const isContainer = n.data.category === 'region';
        const w = isContainer ? ((n.width as number) ?? nodeDim(n.data.category).width) : nodeDim(n.data.category).width;
        const h = isContainer ? ((n.height as number) ?? nodeDim(n.data.category).height) : nodeDim(n.data.category).height;
        minX = Math.min(minX, n.position.x);
        minY = Math.min(minY, n.position.y);
        maxX = Math.max(maxX, n.position.x + w);
        maxY = Math.max(maxY, n.position.y + h);
      }

      // AWS Cloud positioning: we need two things simultaneously —
      //   1. Position AWS Cloud relative to the DX Location band (centered
      //      when its content is shorter than the DX band, top-aligned when
      //      taller — so neither container looks visually stranded).
      //   2. Tight-pack content against the AWS Cloud's inner top so there
      //      is no empty strip above the first child (the thing the user
      //      kept hitting: Tokyo region floating mid-cloud).
      // Step 2 requires shifting ALL awsChildren by the same delta — they
      // were independently barycenter-positioned earlier, which is what
      // created the gap in the first place.
      const dxLocBounds = dxLocContainers
        .filter((n) => !n.data.isRecommended)
        .map((n) => positioned.get(n.id))
        .filter((n): n is DxNode => !!n)
        .reduce<{ top: number; bottom: number } | null>((acc, n) => {
          const y = n.position.y as number;
          const h = (n.height as number | undefined) ?? nodeDim(n.data.category).height;
          if (!acc) return { top: y, bottom: y + h };
          return { top: Math.min(acc.top, y), bottom: Math.max(acc.bottom, y + h) };
        }, null);

      const innerH = maxY - minY;
      const w = maxX - minX + AWS_CLOUD_PAD_X * 2;
      const h = innerH + AWS_CLOUD_PAD_TOP + AWS_CLOUD_PAD_BOTTOM;

      let cloudTopY: number;
      if (dxLocBounds) {
        const dxHeight = dxLocBounds.bottom - dxLocBounds.top;
        if (h <= dxHeight) {
          // Content fits — center AWS Cloud on DX band midpoint.
          cloudTopY = (dxLocBounds.top + dxLocBounds.bottom) / 2 - h / 2;
        } else {
          // Content is taller than DX band — align tops so AWS Cloud
          // overflows downward (never upward into the VPN section).
          cloudTopY = dxLocBounds.top;
        }
      } else {
        cloudTopY = minY - AWS_CLOUD_PAD_TOP;
      }

      // Shift every AWS Cloud descendant so the content's minY lands exactly
      // AWS_CLOUD_PAD_TOP below the container top — no gap, no overlap. Sign
      // is unconstrained (positive = shift down, negative = shift up).
      const targetChildTop = cloudTopY + AWS_CLOUD_PAD_TOP;
      const childShift = targetChildTop - minY;
      if (childShift !== 0) {
        for (const child of awsChildren) {
          const current = positioned.get(child.id);
          if (!current) continue;
          positioned.set(child.id, {
            ...current,
            position: { x: current.position.x, y: current.position.y + childShift },
          });
        }
      }

      setContainer(positioned, awsCloudNode, minX - AWS_CLOUD_PAD_X, cloudTopY, w, h);
    }
  }

  // ---- Step 9.1: Re-center global-column nodes on actual peer positions ----
  // AWS Cloud's shift in Step 9 aligns the cloud block to the DX-location band,
  // but it shifts DXGW/CoreNetwork by the same delta as every other cloud child.
  // DXGW's left-side peers (awsDevices) sit OUTSIDE AWS Cloud inside dxLoc
  // containers, so they don't get that shift — DXGW ends up off its VIF row.
  // Recompute each DXGW's Y against its actual awsDevice neighbours (absolute
  // world coords) so 1-peer DXGWs land on their peer's row and multi-peer
  // DXGWs sit at the barycenter of peers they actually connect to. Applies to
  // both single- and multi-region: the earlier Step 6 barycenter pass runs
  // before Step 9's block shift, so its alignment gets invalidated either way.
  {
    const leftSideCats = new Set(['awsDevice', 'dxPartnerDevice']);
    for (const globalColKey of globalCols) {
      const globalNodes = [...positioned.values()].filter((n) => n.data.category === globalColKey);
      if (globalNodes.length === 0) continue;
      for (const node of globalNodes) {
        const isDxGateway = node.data.category === 'dxGateway';
        const leftYs: number[] = [];
        const allYs: number[] = [];
        for (const e of edges) {
          const otherId = e.source === node.id ? e.target : e.target === node.id ? e.source : null;
          if (!otherId) continue;
          const otherNode = positioned.get(otherId);
          if (!otherNode) continue;
          const dim = nodeDim(otherNode.data.category);
          const centerY = otherNode.position.y + dim.height / 2;
          allYs.push(centerY);
          if (leftSideCats.has(otherNode.data.category)) leftYs.push(centerY);
        }
        const connectedYs = isDxGateway && leftYs.length > 0 ? leftYs : allYs;
        if (connectedYs.length === 0) continue;
        const avgY = connectedYs.reduce((a, b) => a + b, 0) / connectedYs.length;
        const dim = nodeDim(node.data.category);
        const nodeY = Math.max(dxStartY, avgY - dim.height / 2);
        positioned.set(node.id, { ...node, position: { x: node.position.x, y: nodeY } });
      }
      // Collision avoidance: push overlapping siblings down.
      const sortedGlobal = [...positioned.values()]
        .filter((n) => n.data.category === globalColKey)
        .sort((a, b) => a.position.y - b.position.y);
      for (let i = 1; i < sortedGlobal.length; i++) {
        const prev = sortedGlobal[i - 1];
        const curr = sortedGlobal[i];
        const prevDim = nodeDim(prev.data.category);
        const minY = prev.position.y + prevDim.height + vGap;
        if (curr.position.y < minY) {
          positioned.set(curr.id, { ...curr, position: { x: curr.position.x, y: minY } });
          sortedGlobal[i] = { ...curr, position: { x: curr.position.x, y: minY } };
        }
      }
    }
  }

  // ---- Step 9.25 + 9.26: Pin Unattached + Hidden Associations to AWS Cloud bottom ----
  // Both zones are siblings stacked at the bottom of the AWS Cloud container.
  // When the cloud is taller than the regions block (e.g. recommendation mode
  // shifts a DXGW onto a lower ghost-peer row), the zones follow the cloud
  // bottom instead of floating mid-cloud. When regions would otherwise overlap
  // the stack, the cloud grows downward to make room.
  // Row/table heights come from `unattached-zone-dims.ts` and
  // `hidden-assoc-zone-dims.ts` (shared with the renderers) so layout stays in
  // sync with the markup.
  if (unattachedZoneContainer || hiddenAssocZoneContainer) {
    const awsCloud = positioned.get('aws-cloud');
    const awsCloudX = awsCloud ? (awsCloud.position.x as number) : 0;
    const awsCloudY = awsCloud ? (awsCloud.position.y as number) : 0;
    const awsCloudW = awsCloud ? ((awsCloud.width as number | undefined) ?? 600) : 600;
    const awsCloudH = awsCloud ? ((awsCloud.height as number | undefined) ?? 200) : 200;

    // Floor = bottom of the tallest non-zone child of AWS Cloud. DXGWs (and
    // CoreNetwork) live in their own column and can extend below the regions
    // block — if we used only regions bottom as the floor, the stack would
    // overlap the DXGW and the Step 9.5 overlap resolver would shove the
    // DXGW out of the cloud.
    const cloudChildCats = new Set([
      'dxGateway',
      'coreNetwork',
      'region',
    ]);
    const cloudChildren = [...positioned.values()].filter((n) => cloudChildCats.has(n.data.category));
    let cloudChildrenBottom = awsCloudY + 60;
    for (const c of cloudChildren) {
      const h = (c.height as number | undefined) ?? nodeDim(c.data.category, c).height;
      const bottom = (c.position.y as number) + h;
      if (bottom > cloudChildrenBottom) cloudChildrenBottom = bottom;
    }

    const AWS_CLOUD_INNER_PAD = 40;
    const AWS_CLOUD_PAD_BOTTOM = 30;
    const zoneX = awsCloudX + AWS_CLOUD_INNER_PAD;

    let unatH = 0;
    if (unattachedZoneContainer) {
      const vpcChildren = (unattachedZoneContainer.data.vpcChildren ?? []) as Array<unknown>;
      const tgwChildren = (unattachedZoneContainer.data.tgwChildren ?? []) as Array<unknown>;
      const vgwChildren = (unattachedZoneContainer.data.vgwChildren ?? []) as Array<unknown>;
      const dxgwChildren = (unattachedZoneContainer.data.dxgwChildren ?? []) as Array<unknown>;
      unatH = zoneHeight(
        vpcChildren.length,
        tgwChildren.length,
        expandedUnattachedZone,
        vgwChildren.length,
        dxgwChildren.length,
      );
    }

    let haH = 0;
    if (hiddenAssocZoneContainer) {
      const rows = ((hiddenAssocZoneContainer.data.hiddenAssocChildren ?? []) as Array<unknown>).length;
      haH = hiddenAssocZoneHeight(rows, expandedHiddenAssocZone);
    }

    const interZoneGap = (unatH > 0 && haH > 0) ? HIDDEN_ASSOC_ZONE_DIMS.marginTop : 0;
    const stackHeight = unatH + interZoneGap + haH;

    // Minimum Y where the stack can start without overlapping any cloud child.
    const minStackTop = cloudChildrenBottom + ZONE_DIMS.marginTop;
    // Preferred stack top when pinned to AWS Cloud bottom.
    const pinnedStackTop = awsCloudY + awsCloudH - AWS_CLOUD_PAD_BOTTOM - stackHeight;

    let stackTop: number;
    let newCloudH = awsCloudH;
    if (pinnedStackTop >= minStackTop) {
      // AWS Cloud already has room — pin flush to its bottom.
      stackTop = pinnedStackTop;
    } else {
      // Regions push the stack past the current cloud bottom — grow the cloud.
      stackTop = minStackTop;
      newCloudH = stackTop + stackHeight + AWS_CLOUD_PAD_BOTTOM - awsCloudY;
    }

    let cursorY = stackTop;
    if (unattachedZoneContainer) {
      const zoneW = Math.max(ZONE_DIMS.minWidth, awsCloudW - AWS_CLOUD_INNER_PAD * 2);
      const zoneWithState: DxNode = {
        ...unattachedZoneContainer,
        data: { ...unattachedZoneContainer.data, isExpanded: expandedUnattachedZone },
      };
      setContainer(positioned, zoneWithState, zoneX, cursorY, zoneW, unatH);
      cursorY += unatH + interZoneGap;
    }
    if (hiddenAssocZoneContainer) {
      const zoneW = Math.max(HIDDEN_ASSOC_ZONE_DIMS.minWidth, awsCloudW - AWS_CLOUD_INNER_PAD * 2);
      const zoneWithState: DxNode = {
        ...hiddenAssocZoneContainer,
        data: { ...hiddenAssocZoneContainer.data, isExpanded: expandedHiddenAssocZone },
      };
      setContainer(positioned, zoneWithState, zoneX, cursorY, zoneW, haH);
    }

    if (awsCloud && newCloudH !== awsCloudH) {
      setContainer(positioned, awsCloud, awsCloudX, awsCloudY, awsCloudW, newCloudH);
    }
  }

  // ---- Step 9.5: 2D axis-aligned bounding box overlap resolution ----
  // The Step 6 passes resolve overlaps vertically only. With the new tgwConnect
  // column and orphan-TGW handling, leaf nodes can still end up with overlapping
  // 2D rects (e.g. a TGW Connect node drifting into a VPC group, or an orphan TGW
  // edge-routing into an adjacent column). This pass nudges any overlapping leaf
  // nodes apart along the axis of smallest separation. Containers are skipped
  // (they'd de-parent their children if shifted here — that re-wrap happens below).
  {
    const containerCats = new Set(['customerSite', 'dxLocation', 'region', 'awsCloud']);
    const leafNodes = [...positioned.values()].filter((n) => !containerCats.has(n.data.category));
    const MIN_GAP = 6; // px — cosmetic breathing room after separation
    const MAX_PASSES = 3;

    type Rect = { id: string; x: number; y: number; w: number; h: number };
    const getRect = (id: string): Rect => {
      const n = positioned.get(id)!;
      const dim = nodeDim(n.data.category, n);
      return { id, x: n.position.x, y: n.position.y, w: dim.width, h: dim.height };
    };

    // Cross-region leaf nodes are laid out into columns that share X with
    // neighbours in other regions. Step 6e packs regions vertically, but that
    // can leave nodes in the same column with tiny residual overlaps in Y —
    // which this pairwise resolver will cascade-push down, inflating gaps
    // inside the LATER region (e.g. Tokyo's tail VPCs would get shoved 200px+
    // past their tight-packed Y). Skip nodes in the SAME column but in
    // DIFFERENT regions — their vertical spacing is the job of Step 6e.
    const regionById = new Map<string, string>();
    for (const n of leafNodes) {
      const reg = getNodeRegion(n);
      if (reg !== '_default') regionById.set(n.id, reg);
    }
    const inSameColumn = (a: DxNode, b: DxNode) => {
      // Two leaves are in the "same column" if their category belongs to the
      // same COLUMN_DEFS entry. Use the same keys defined in columnGroups above.
      for (const col of COLUMN_DEFS) {
        if (col.categories.includes(a.data.category) && col.categories.includes(b.data.category)) return true;
      }
      return false;
    };
    for (let pass = 0; pass < MAX_PASSES; pass++) {
      let moved = false;
      for (let i = 0; i < leafNodes.length; i++) {
        const a = getRect(leafNodes[i].id);
        for (let j = i + 1; j < leafNodes.length; j++) {
          const b = getRect(leafNodes[j].id);
          const overlapX = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
          const overlapY = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
          if (overlapX <= 0 || overlapY <= 0) continue;
          const aReg = regionById.get(leafNodes[i].id);
          const bReg = regionById.get(leafNodes[j].id);
          if (aReg && bReg && aReg !== bReg && inSameColumn(leafNodes[i], leafNodes[j])) continue;
          // Push along the axis of smaller overlap so we do the least damage.
          const pushVertical = overlapY < overlapX;
          const bNode = positioned.get(b.id)!;
          if (pushVertical) {
            const bCenter = b.y + b.h / 2;
            const aCenter = a.y + a.h / 2;
            const shift = (bCenter >= aCenter ? 1 : -1) * (overlapY + MIN_GAP);
            positioned.set(b.id, { ...bNode, position: { x: bNode.position.x, y: bNode.position.y + shift } });
          } else {
            const bCenter = b.x + b.w / 2;
            const aCenter = a.x + a.w / 2;
            const shift = (bCenter >= aCenter ? 1 : -1) * (overlapX + MIN_GAP);
            positioned.set(b.id, { ...bNode, position: { x: bNode.position.x + shift, y: bNode.position.y } });
          }
          moved = true;
        }
      }
      if (!moved) break;
    }
  }

  // ---- Final pass: set explicit width AND height on leaf nodes so React Flow renders them
  // at the dimensions the layout engine assumes. Without an explicit height, nodes hug their
  // content — and content differs (cross-account nodes get an extra "Account:" line, live-status
  // adds a state dot). Same category → different rendered height → inconsistent visible gaps
  // between sibling nodes because the layout places centers at fixed NODE_DIMENSIONS intervals.
  // BaseNode's inner card stretches to fill via minHeight: 100%. ----
  for (const [id, node] of positioned) {
    if (containerCategories.has(node.data.category) || node.data.category === 'awsCloud') continue;
    const dim = nodeDim(node.data.category, node);
    positioned.set(id, {
      ...node,
      width: dim.width,
      height: dim.height,
      style: { ...node.style, width: dim.width, height: dim.height },
    });
  }

  // ---- Step 10: Convert to React Flow parent/child grouping ----
  // Set parentId on child nodes and convert absolute → relative positions.
  // This enables native drag-with-children and proper z-ordering.

  // customerSite → onPremise (DX on-prem via siteCompanionOnpremId, plus any
  // VPN routers hosted inside a DX site via details.hostSiteId — same parent
  // conversion so React Flow can drag the whole zone as one unit).
  for (const site of customerSiteContainers) {
    const siteNode = positioned.get(site.id);
    if (!siteNode) continue;
    const childIds: string[] = [];
    const companionId = siteCompanionOnpremId(site.id);
    if (companionId) childIds.push(companionId);
    for (const vpn of hostedVpnByHostId.get(site.id) ?? []) childIds.push(vpn.id);
    for (const childId of childIds) {
      const child = positioned.get(childId);
      if (!child) continue;
      positioned.set(childId, {
        ...child,
        parentId: site.id,
        position: {
          x: child.position.x - siteNode.position.x,
          y: child.position.y - siteNode.position.y,
        },
      });
    }
  }

  // dxLocation → dxPartnerDevice, awsDevice
  for (const loc of dxLocContainers) {
    const locNode = positioned.get(loc.id);
    if (!locNode) continue;
    const locCode = getContainerCode(loc);
    const children = childrenByLoc.get(locCode) ?? [];
    for (const child of children) {
      const childNode = positioned.get(child.id);
      if (!childNode) continue;
      positioned.set(child.id, {
        ...childNode,
        parentId: loc.id,
        position: {
          x: childNode.position.x - locNode.position.x,
          y: childNode.position.y - locNode.position.y,
        },
      });
    }
  }

  // region → tgw, tgwGroup, tgwConnect, vgw, vpc, vpcGroup, cgw (MUST happen before awsCloud conversion)
  const regionChildCats = new Set(['tgw', 'tgwGroup', 'isolatedTgwGroup', 'tgwConnect', 'vgw', 'vpc', 'vpcGroup', 'cgw']);
  for (const region of regionContainers) {
    const regionNode = positioned.get(region.id);
    if (!regionNode) continue;
    const regionCode = (region.data.details as Record<string, string>)?.regionCode ?? '';
    for (const [id, node] of positioned) {
      if (!regionChildCats.has(node.data.category)) continue;
      if (node.data.isRecommended && !region.data.isRecommended) continue;
      const nodeRegion = (node.data.details as Record<string, string>)?.region;
      const isChild = nodeRegion ? nodeRegion === regionCode : id.includes(regionCode);
      if (!isChild) continue;
      positioned.set(id, {
        ...node,
        parentId: region.id,
        position: {
          x: node.position.x - regionNode.position.x,
          y: node.position.y - regionNode.position.y,
        },
      });
    }
  }

  // awsCloud → dxGateway, coreNetwork, region, unattachedZone (outermost parent, done last)
  const awsCloudPositioned = positioned.get('aws-cloud');
  if (awsCloudPositioned) {
    const awsChildCats = new Set(['dxGateway', 'coreNetwork', 'region', 'unattachedZone', 'hiddenAssocZone']);
    for (const [id, node] of positioned) {
      if (!awsChildCats.has(node.data.category)) continue;
      positioned.set(id, {
        ...node,
        parentId: 'aws-cloud',
        position: {
          x: node.position.x - awsCloudPositioned.position.x,
          y: node.position.y - awsCloudPositioned.position.y,
        },
      });
    }
  }

  // Return nodes sorted: parents before children (React Flow requirement)
  // Use depth-based sort for 3-level nesting: awsCloud(0) → region(1) → tgw(2)
  // Filter out empty region containers that have no visible child nodes.
  const result = nodes
    .filter((n) => !emptyRegionIds.has(n.id))
    .map((n) => positioned.get(n.id) ?? n);
  const parentIds = new Set(result.filter((n) => !n.parentId).map((n) => n.id));
  result.sort((a, b) => {
    const depthA = !a.parentId ? 0 : parentIds.has(a.parentId) ? 1 : 2;
    const depthB = !b.parentId ? 0 : parentIds.has(b.parentId) ? 1 : 2;
    return depthA - depthB;
  });
  return result;
}
