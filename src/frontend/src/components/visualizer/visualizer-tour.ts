"use client";

/**
 * Visualizer tour — ported from the source SPA's `GuidedTour.tsx`.
 *
 * Relies on `data-tour="<name>"` selectors attached to FlowCanvas / toolbar
 * elements. Steps whose target isn't mounted (e.g. maintenance calendar
 * when the topology has no events) get filtered out by `TourAutoStarter`
 * so the tour doesn't dead-end.
 */

import type { DriveStep } from "driver.js";
import { registerTour } from "@/lib/tours/tour-registry";

const steps: DriveStep[] = [
  {
    popover: {
      title: "Welcome to the DX visualizer",
      description:
        "A quick tour of what each part of the topology panel does. Skip anytime — re-run it from the ? button in the chat header.",
    },
  },
  {
    element: '[data-tour="overlays"]',
    popover: {
      title: "Canvas overlays",
      description:
        "Three toggles layered onto the topology: **Simulate** lets you click nodes or edges to fail them and see surviving paths; **Live Status** polls CloudWatch for BGP metrics every 60s; **Recommendation** reveals ghost nodes (green, dashed) for the next SLA tier.",
      side: "bottom",
      align: "center",
    },
  },
  {
    element: '[data-tour="lock"]',
    popover: {
      title: "Lock / unlock the canvas",
      description:
        "Unlock (green) to drag nodes and rearrange the layout. Lock (red) freezes positions — labels, IDs, CIDRs, and ASNs inside nodes become selectable text you can copy.",
      side: "right",
      align: "start",
    },
  },
  {
    element: '[data-tour="scorecard"]',
    popover: {
      title: "Resilience Status",
      description:
        "Open action items for your topology — current SLA tier per DX Gateway, upgrade options, and best-practice checks. Expand to pick a target tier or open a full-screen view. Hovering a gateway row spotlights the matching node on the canvas.",
      side: "right",
      align: "end",
    },
  },
  {
    element: '[data-tour="maintenance"]',
    popover: {
      title: "Planned maintenance",
      description:
        "Calendar of upcoming AWS-scheduled maintenance events affecting your Direct Connect resources. Click a day to see affected connections, VIFs, and gateways.",
      side: "bottom",
      align: "end",
    },
  },
  {
    element: '[data-tour="generate-report"]',
    popover: {
      title: "Generate a full report",
      description:
        "Run the `dx_resiliency_report` template — a 6-section review covering per-DXGW scoring, critical findings, ghost-node recommendations, best practices, and cost-to-reach-target. Opens in the artifact panel on the right.",
      side: "bottom",
      align: "end",
    },
  },
];

registerTour({
  id: "visualizer",
  label: "Visualizer tour",
  steps,
});
