"use client";

/**
 * React-Flow-specific topology image export.
 *
 * Companion to the generic `useExportImage` — exports the full topology
 * graph (not just the visible viewport), resolved to ~4K width so small
 * details stay crisp when embedded in reports or chat threads.
 *
 * Ported from the source SPA's `useExportImage.ts`. Kept separate from the
 * generic hook because it depends on `@xyflow/react` helpers for node-bounds
 * math and applies a transform that expands off-screen graph regions into
 * the capture frame.
 */

import { useCallback } from "react";
import { getNodesBounds, getViewportForBounds, useReactFlow } from "@xyflow/react";
import { toPng } from "html-to-image";
import { useTopologyStore } from "@/lib/topology/store";

const TARGET_WIDTH_4K = 3840;
const PADDING = 80;

function timestamp(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-` +
    `${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`
  );
}

export function useExportTopologyImage() {
  const { getNodes } = useReactFlow();
  const theme = useTopologyStore((s) => s.theme);

  return useCallback(async () => {
    const viewport = document.querySelector<HTMLElement>(".react-flow__viewport");
    if (!viewport) return;

    const nodes = getNodes();
    if (nodes.length === 0) return;

    const bounds = getNodesBounds(nodes);
    const width = Math.ceil(bounds.width + PADDING * 2);
    const height = Math.ceil(bounds.height + PADDING * 2);

    const transform = getViewportForBounds(bounds, width, height, 0.5, 2, 0);
    const background = theme === "light" ? "#eef1f6" : "#0f172a";

    const filter = (node: Element) => {
      if (!(node instanceof Element) || !node.classList) return true;
      const cls = node.classList;
      return (
        !cls.contains("react-flow__minimap") &&
        !cls.contains("react-flow__controls") &&
        !cls.contains("react-flow__panel") &&
        !cls.contains("react-flow__background") &&
        !cls.contains("sim-canvas-frame")
      );
    };

    const pixelRatio = Math.min(4, Math.max(2, TARGET_WIDTH_4K / width));

    const dataUrl = await toPng(viewport, {
      backgroundColor: background,
      width,
      height,
      pixelRatio,
      filter,
      cacheBust: true,
      // Skip cross-origin @font-face inlining — Google Fonts 403s the
      // `cssRules` read. Node labels fall back to the system font, which
      // is acceptable for exported topology snapshots.
      skipFonts: true,
      style: {
        width: `${width}px`,
        height: `${height}px`,
        transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.zoom})`,
      },
    });

    const link = document.createElement("a");
    link.download = `topology-${timestamp()}.png`;
    link.href = dataUrl;
    link.click();
  }, [getNodes, theme]);
}
