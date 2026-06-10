// Single source of truth for the Unattached-zone container dimensions.
// Both the layout engine (Step 9.25) and UnattachedZoneNode read from here so
// the rendered body matches the reserved container box. When UnattachedZoneNode's
// markup changes, update the constants below and both sides stay in sync.
//
// Measured against the rendered DOM (Playwright getBoundingClientRect):
//   header button h=40, body padY=10+10, section title h=16.5 + mb=6,
//   thead h=24, tbody row h=24. Add 2px slack per row so sub-pixel rounding
//   on zoom or different font rendering can't clip the last row.
export const ZONE_DIMS = {
  headerH: 40,
  bodyPadY: 20,
  sectionLabelH: 24,
  tableHeaderH: 25,
  tableRowH: 25,
  tablesGap: 12,
  marginTop: 36,
  minWidth: 500,
};

/**
 * Total height of the zone container given row counts per table and whether
 * the zone is expanded. Counts are passed in the order the renderer draws the
 * sections (DXGWs → VGWs → VPCs → TGWs).
 */
export function zoneHeight(
  vpcCount: number,
  tgwCount: number,
  expanded: boolean,
  vgwCount = 0,
  dxgwCount = 0
): number {
  if (!expanded) return ZONE_DIMS.headerH;
  const sectionH = (rows: number) =>
    ZONE_DIMS.sectionLabelH + ZONE_DIMS.tableHeaderH + rows * ZONE_DIMS.tableRowH;
  const counts = [dxgwCount, vgwCount, vpcCount, tgwCount].filter((c) => c > 0);
  let content = 0;
  counts.forEach((c, i) => {
    if (i > 0) content += ZONE_DIMS.tablesGap;
    content += sectionH(c);
  });
  return ZONE_DIMS.headerH + ZONE_DIMS.bodyPadY + content;
}
