"use client";

/**
 * Generic "download this DOM subtree as a PNG" hook.
 *
 * Promoted from the network-resilience visualizer's narrow `useExportImage`
 * so every panel with exportable content (Trace, Artifact, Visualizer) can
 * share one implementation. The visualizer's React-Flow-specific export
 * (bounds-aware full-graph rasterization) lives separately in
 * `use-export-topology-image.ts` — it needs ReactFlow helpers that don't
 * generalize.
 *
 * Usage:
 *   const exportImage = useExportImage();
 *   exportImage({ element: ref.current, filename: "trace-foo.png" });
 *
 * Browsers surface a save dialog automatically (uses the downloadable
 * anchor-click pattern — no file-system permissions needed).
 */

import { useCallback } from "react";
import { toPng } from "html-to-image";

export interface ExportImageOptions {
  /** DOM node to rasterize. The full subtree is captured. */
  element: HTMLElement | null;
  /** File name including .png extension. */
  filename: string;
  /** Optional background color. Defaults to the parent's `--bg-primary`. */
  backgroundColor?: string;
  /**
   * Device pixel ratio; higher = sharper output but bigger file. Defaults to
   * 2 for retina-quality screenshots.
   */
  pixelRatio?: number;
  /**
   * Filter predicate passed to `html-to-image`. Return false to strip the
   * element from the rasterized output. Useful for hiding close buttons,
   * resize handles, etc.
   */
  filter?: (node: Element) => boolean;
}

function timestamp(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-` +
    `${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`
  );
}

function resolveBackground(): string {
  if (typeof window === "undefined") return "#ffffff";
  const styles = getComputedStyle(document.documentElement);
  return styles.getPropertyValue("--bg-primary").trim() || "#ffffff";
}

export function useExportImage() {
  return useCallback(async (opts: ExportImageOptions) => {
    const { element, filename, backgroundColor, pixelRatio = 2, filter } = opts;
    if (!element) return;
    const dataUrl = await toPng(element, {
      backgroundColor: backgroundColor ?? resolveBackground(),
      pixelRatio,
      cacheBust: true,
      filter,
      // html-to-image tries to inline every @font-face it can find so the
      // rasterized image matches the live DOM. Google Fonts stylesheets are
      // served cross-origin, which trips CORS on `cssRules` access and
      // spams the console. Disable font embedding — the rendered PNG just
      // falls back to the user's system fonts, which is acceptable for
      // screenshots of trace/report content.
      skipFonts: true,
    });
    const link = document.createElement("a");
    link.download = filename;
    link.href = dataUrl;
    link.click();
  }, []);
}

/** Suggests a timestamped filename using the given prefix (no path). */
export function suggestImageFilename(prefix: string): string {
  return `${prefix}-${timestamp()}.png`;
}
