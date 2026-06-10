"use client";

/**
 * VisualizerCard — inline chat card that teases the topology/assessment
 * and opens the VisualizerPanel on click. Mirrors the ArtifactCard
 * pattern used for reports.
 *
 * Rendered by Thread.tsx when a message's text part matches the
 * ``<visualizer-state>`` tag. Clicking dispatches ``open-visualizer``
 * with the current message ID so page.tsx can mount the panel.
 */

import { useMessage } from "@assistant-ui/react";
import { Network, ChevronRight } from "lucide-react";
import type { TopologyData, CombinedAssessment } from "@/lib/topology";

const VIZ_STATE_RE = /^<visualizer-state>([\s\S]*)<\/visualizer-state>$/;

interface VisualizerStatePayload {
  topology?: TopologyData;
  assessment?: CombinedAssessment;
  toolName?: string;
}

export function VisualizerCard({ payloadText }: { payloadText: string }) {
  const message = useMessage();
  const m = payloadText.match(VIZ_STATE_RE);
  if (!m) return null;

  let parsed: VisualizerStatePayload | null = null;
  try {
    parsed = JSON.parse(m[1]) as VisualizerStatePayload;
  } catch {
    return null;
  }
  if (!parsed) return null;

  const topology = parsed.topology;
  const assessment = parsed.assessment;
  const conns = topology?.connections?.length ?? 0;
  const dxgws = topology?.dxGateways?.length ?? 0;
  const vpcs = topology?.vpcs?.length ?? 0;
  const regions = new Set([
    ...((topology?.connections ?? []).map((c) => c.region)),
    ...((topology?.vpcs ?? []).map((v) => v.region)),
  ]);
  regions.delete(undefined as unknown as string);

  const title = parsed.toolName === "assess_dx_resiliency"
    ? "Direct Connect resiliency assessment"
    : "Direct Connect topology";

  const score = assessment?.resiliency?.score;
  const tier = assessment?.resiliency?.currentLevel;

  const onClick = () => {
    window.dispatchEvent(
      new CustomEvent("open-visualizer", {
        detail: { messageId: message.id },
      }),
    );
  };

  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-left rounded-lg p-3 my-2 transition-colors hover:bg-[var(--bg-elevated)]"
      style={{
        background: "var(--bg-surface)",
        border: "1px solid var(--border-subtle)",
      }}
    >
      <div className="flex items-start gap-3">
        <div
          className="flex-shrink-0 p-2 rounded-md"
          style={{
            background: "var(--bg-elevated)",
            color: "var(--accent-ai, var(--accent))",
          }}
        >
          <Network className="h-4 w-4" aria-hidden="true" />
        </div>
        <div className="flex-1 min-w-0">
          <div
            className="text-sm font-medium"
            style={{ color: "var(--text-primary)" }}
          >
            {title}
          </div>
          <div
            className="text-xs mt-0.5"
            style={{ color: "var(--text-muted)" }}
          >
            {conns} connection{conns === 1 ? "" : "s"} · {dxgws} DX gateway
            {dxgws === 1 ? "" : "s"} · {vpcs} VPC{vpcs === 1 ? "" : "s"}
            {regions.size > 0 && ` · ${regions.size} region${regions.size === 1 ? "" : "s"}`}
          </div>
          {score !== undefined && tier && (
            <div
              className="text-xs mt-1 font-mono"
              style={{ color: "var(--text-secondary)" }}
            >
              Score {score}/100 · tier:{" "}
              <span className="capitalize">{tier}</span>
            </div>
          )}
          <div
            className="text-xs mt-1.5 flex items-center gap-0.5"
            style={{ color: "var(--accent-ai, var(--accent))" }}
          >
            Open visualizer
            <ChevronRight className="h-3 w-3" aria-hidden="true" />
          </div>
        </div>
      </div>
    </button>
  );
}
