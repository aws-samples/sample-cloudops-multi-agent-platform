"use client";

/**
 * Interactive controls for the visualizer panel — slim port of the source SPA's
 * `TopBar.tsx`. Hosts overlay toggles (Simulate, Live Status, Utilization +
 * 30/60/90 window selector, Recommendation) plus the `ViewToggle` /
 * target-tier pickers. Utilization is gated to surface only when Live is
 * on — each utilization fetch bills CloudWatch GetMetricData so we keep
 * it behind both toggles plus a per-window cache in `useUtilization`.
 *
 * Source extras NOT ported here:
 * - Scenario picker (parent app doesn't run mock topologies)
 * - Connect-AWS / SSO controls (parent uses Cognito)
 * - Refresh-topology button — the agent owns topology discovery; the user
 *   re-invokes via chat ("refresh the topology"). If this turns out to be
 *   a common action we can add a chat-trigger button in Phase 6.5.
 * - "Take a tour" — Phase 8 scope
 *
 * The "..." overflow menu hosts Generate Report + Export topology as PNG.
 * PNG export dispatches `visualizer-export-png` because the underlying hook
 * (`useExportTopologyImage`) needs `ReactFlowProvider` context, which only
 * exists inside `FlowCanvas`.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Zap,
  Activity,
  BarChart3,
  ChevronDown,
  ShieldCheck,
  FileText,
  MoreHorizontal,
  Image as ImageIcon,
} from "lucide-react";
import { useTopologyStore, useIsLight } from "@/lib/topology/store";
import { ViewToggle } from "./ViewToggle";
import { MaintenanceCalendar } from "./MaintenanceCalendar";

export function VisualizerToolbar() {
  const light = useIsLight();
  const isSimulating = useTopologyStore((s) => s.isSimulating);
  const setIsSimulating = useTopologyStore((s) => s.setIsSimulating);
  const showLiveStatus = useTopologyStore((s) => s.showLiveStatus);
  const toggleLiveStatus = useTopologyStore((s) => s.toggleLiveStatus);
  const showUtilization = useTopologyStore((s) => s.showUtilization);
  const toggleUtilization = useTopologyStore((s) => s.toggleUtilization);
  const utilizationWindow = useTopologyStore((s) => s.utilizationWindow);
  const setUtilizationWindow = useTopologyStore((s) => s.setUtilizationWindow);
  const viewMode = useTopologyStore((s) => s.viewMode);
  const setViewMode = useTopologyStore((s) => s.setViewMode);
  const failedNodeIds = useTopologyStore((s) => s.failedNodeIds);
  const failedEdgeIds = useTopologyStore((s) => s.failedEdgeIds);
  const clearFailures = useTopologyStore((s) => s.clearFailures);
  const currentEdges = useTopologyStore((s) => s.currentEdges);

  const hasFailures = failedNodeIds.size > 0 || failedEdgeIds.size > 0;

  // Lookback window dropdown — closes on outside click so it doesn't trap
  // focus when the user moves on. Mirrors the source SPA's TopBar pattern.
  const [windowOpen, setWindowOpen] = useState(false);
  const windowRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!windowOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!windowRef.current?.contains(e.target as Node)) setWindowOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [windowOpen]);

  // Overflow "..." menu — combines Generate Report + Export PNG. PNG export
  // can't live here directly because `useExportTopologyImage` needs
  // `ReactFlowProvider` context, which only exists inside `FlowCanvas`. The
  // menu dispatches a CustomEvent that `FlowCanvasInner` listens for.
  const [actionsOpen, setActionsOpen] = useState(false);
  const actionsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!actionsOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!actionsRef.current?.contains(e.target as Node)) setActionsOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [actionsOpen]);

  const exportTopologyImage = useCallback(() => {
    window.dispatchEvent(new CustomEvent("visualizer-export-png"));
  }, []);

  // "Generate report" fires an adhoc-report event: the supervisor writes a
  // comprehensive DX resilience review in the current thread, grounded in the
  // topology + assessment already in memory. Runs as a single chat turn in
  // report mode (no template_id, no multi-section fan-out) — the frontend's
  // report-mode wrapper streams tokens into the ReportPanel.
  //
  // Staying in the current thread is essential: memory has the discover/assess
  // results; jumping to a fresh thread would either waste tokens re-fetching
  // or (worse) produce a "no topology found" report against live AWS.
  //
  // `mockScenario` (if present on the topology object) surfaces the demo-data
  // caveat into the prompt so the supervisor writes the caveat into the
  // executive summary instead of presenting mock account IDs as real.
  const generateReport = useCallback(() => {
    const topo = useTopologyStore.getState().topologyData;
    const mockScenario = (topo as { mockScenario?: string } | null)?.mockScenario;
    const mockCaveat = mockScenario
      ? `\n\nContext: the topology in this conversation was loaded from the "${mockScenario}" mock scenario — make this explicit in the executive summary so the reader knows these numbers are demo data, not their live AWS environment.`
      : "";
    const prompt =
      "Write a comprehensive Direct Connect Resilience Review report for the " +
      "topology already in this conversation's memory. Do NOT re-run " +
      "`discover_dx_topology` or `assess_dx_resiliency` — the results from " +
      "the prior turn(s) are what this report is about.\n\n" +
      "IMPORTANT: Resiliency is reported PER DX GATEWAY using the tier " +
      "(none / devtest / high / maximum). There is NO aggregate /100 score " +
      "for the account — do NOT invent one or quote an \"overall resiliency " +
      "score\". Summarise across gateways by describing the tier distribution " +
      "(e.g. \"2 at Maximum, 1 at High\").\n\n" +
      "Structure the report with these sections in order:\n\n" +
      "1. Executive Summary — DXGW tier distribution, total gateway count, 3 top findings\n" +
      "2. Per-DXGW Assessment — for each gateway: name/id, current tier, target tier, location/connection/device counts, findings\n" +
      "3. Critical Findings — every critical + warning recommendation with severity, title, description, affected resources\n" +
      "4. Recommended Infrastructure Changes — ghost-node recommendations grouped by target tier\n" +
      "5. Best Practices Checklist — table with Practice | Status (✅/⚠️/⚪) | Affected DXGW | Notes\n" +
      "6. Cost to Reach Target Tier — use `estimate_upgrade_cost` per DXGW, produce a table and total monthly delta\n\n" +
      "Format as clean markdown. Start with a `#` title heading. Do not include follow-up questions." +
      mockCaveat;
    window.dispatchEvent(
      new CustomEvent("generate-adhoc-report", { detail: { prompt } }),
    );
  }, []);

  // Surviving-path summary shown in the simulation banner.
  const impactSummary = useMemo(() => {
    if (!isSimulating || !hasFailures) return null;
    const totalEdges = currentEdges.filter((e) => !e.data?.isRecommended).length;
    let downEdges = 0;
    for (const e of currentEdges) {
      if (e.data?.isRecommended) continue;
      if (failedEdgeIds.has(e.id) || failedNodeIds.has(e.source) || failedNodeIds.has(e.target)) {
        downEdges++;
      }
    }
    return {
      totalEdges,
      downEdges,
      upEdges: totalEdges - downEdges,
      failedNodes: failedNodeIds.size,
      failedLinks: failedEdgeIds.size,
    };
  }, [isSimulating, hasFailures, failedNodeIds, failedEdgeIds, currentEdges]);

  return (
    <>
      <div
        className={`flex items-center justify-between px-3 py-2 border-b transition-colors ${
          isSimulating
            ? light
              ? "bg-red-50/70 border-red-200/70"
              : "bg-red-950/30 border-red-800/30"
            : ""
        }`}
        style={{
          borderColor: isSimulating ? undefined : "var(--border-subtle)",
          background: isSimulating ? undefined : "var(--bg-secondary)",
        }}
      >
        {/* Overlays */}
        <div
          className={`flex items-center gap-0.5 rounded-lg p-0.5 ${
            light ? "bg-gray-100/80" : "bg-white/[0.04]"
          }`}
          data-tour="overlays"
        >
          <button
            type="button"
            onClick={toggleLiveStatus}
            className={`flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-medium rounded-md transition-all ${
              showLiveStatus
                ? light
                  ? "bg-emerald-100 text-emerald-700 shadow-sm"
                  : "bg-emerald-500/15 text-emerald-300"
                : light
                  ? "text-gray-600 hover:text-gray-800 hover:bg-white"
                  : "text-slate-300 hover:text-slate-100 hover:bg-white/5"
            }`}
            title={showLiveStatus ? "Hide live status" : "Show live status"}
            aria-pressed={showLiveStatus}
          >
            <Activity className="w-3 h-3" aria-hidden="true" />
            Live
          </button>
          {/* Utilization is a standalone overlay (matches reference). Each
              fetch bills CloudWatch so `useUtilization` only fires when the
              toggle is on. */}
          <button
            type="button"
            onClick={toggleUtilization}
            className={`flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-medium rounded-md transition-all ${
              showUtilization
                ? light
                  ? "bg-emerald-100 text-emerald-700 shadow-sm"
                  : "bg-emerald-500/15 text-emerald-300"
                : light
                  ? "text-gray-600 hover:text-gray-800 hover:bg-white"
                  : "text-slate-300 hover:text-slate-100 hover:bg-white/5"
            }`}
            title={
              showUtilization
                ? "Hide CloudWatch utilization"
                : "Show CloudWatch utilization (peak over window)"
            }
            aria-pressed={showUtilization}
          >
            <BarChart3 className="w-3 h-3" aria-hidden="true" />
            Utilization
          </button>
          {showUtilization && (
            <div ref={windowRef} className="relative">
              <button
                type="button"
                onClick={() => setWindowOpen(!windowOpen)}
                className={`flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md transition-colors ${
                  light
                    ? "bg-gray-100 text-gray-700 hover:bg-gray-200/80"
                    : "bg-white/[0.08] text-slate-300 hover:bg-white/[0.12]"
                }`}
                title="Lookback window"
                aria-haspopup="menu"
                aria-expanded={windowOpen}
              >
                {utilizationWindow}d
                <ChevronDown
                  className={`w-3 h-3 opacity-50 transition-transform ${windowOpen ? "rotate-180" : ""}`}
                  aria-hidden="true"
                />
              </button>
              {windowOpen && (
                <div
                  role="menu"
                  className={`absolute top-full left-0 mt-1 py-1 rounded-lg shadow-lg border z-50 min-w-[110px] ${
                    light
                      ? "bg-white border-gray-200 shadow-gray-200/50"
                      : "bg-slate-800 border-slate-700 shadow-black/40"
                  }`}
                >
                  {([30, 60, 90] as const).map((d) => (
                    <button
                      key={d}
                      role="menuitem"
                      type="button"
                      onClick={() => {
                        setUtilizationWindow(d);
                        setWindowOpen(false);
                      }}
                      className={`w-full text-left px-3 py-1.5 text-[11px] transition-colors ${
                        utilizationWindow === d
                          ? light
                            ? "bg-blue-50 text-blue-600 font-semibold"
                            : "bg-blue-500/15 text-blue-400 font-semibold"
                          : light
                            ? "text-gray-700 hover:bg-gray-50"
                            : "text-slate-300 hover:bg-white/[0.06]"
                      }`}
                    >
                      Last {d} days
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          <button
            type="button"
            onClick={() => setViewMode(viewMode === "recommended" ? "current" : "recommended")}
            className={`flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-medium rounded-md transition-all ${
              viewMode === "recommended"
                ? "bg-emerald-600 text-white shadow-sm shadow-emerald-500/25"
                : light
                  ? "text-gray-600 hover:text-gray-800 hover:bg-white"
                  : "text-slate-300 hover:text-slate-100 hover:bg-white/5"
            }`}
            title={
              viewMode === "recommended"
                ? "Switch to current state view"
                : "Show recommendations"
            }
            aria-pressed={viewMode === "recommended"}
          >
            <ShieldCheck className="w-3 h-3" aria-hidden="true" />
            Recommendation
          </button>
        </div>

        {/* Tier pickers — renders conditionally based on viewMode + topology shape */}
        <div className="flex items-center gap-1.5">
          <ViewToggle />
          {isSimulating && hasFailures && (
            <button
              type="button"
              onClick={clearFailures}
              className="px-2 py-1 text-[10px] font-medium text-red-400 hover:text-red-300 rounded-md hover:bg-red-500/10 transition-colors"
              title="Clear all simulated failures"
            >
              Reset
            </button>
          )}
          <div ref={actionsRef} className="relative" data-tour="generate-report">
            <button
              type="button"
              onClick={() => setActionsOpen((v) => !v)}
              title="More actions"
              aria-label="More actions"
              aria-haspopup="menu"
              aria-expanded={actionsOpen}
              className={`flex items-center justify-center w-8 h-8 rounded-lg transition-all ${
                isSimulating
                  ? "bg-red-500 text-white shadow-sm shadow-red-500/25"
                  : actionsOpen
                    ? light
                      ? "bg-gray-200/80 text-gray-700"
                      : "bg-slate-700 text-slate-200"
                    : light
                      ? "text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                      : "text-slate-400 hover:bg-white/5 hover:text-slate-200"
              }`}
            >
              <MoreHorizontal className="w-4 h-4" aria-hidden="true" />
            </button>
            {actionsOpen && (
              <div
                role="menu"
                className={`absolute top-full right-0 mt-1 py-1 rounded-lg shadow-lg border z-50 min-w-[200px] ${
                  light
                    ? "bg-white border-gray-200 shadow-gray-200/50"
                    : "bg-slate-800 border-slate-700 shadow-black/40"
                }`}
              >
                <button
                  role="menuitemcheckbox"
                  aria-checked={isSimulating}
                  type="button"
                  onClick={() => {
                    setActionsOpen(false);
                    setIsSimulating(!isSimulating);
                  }}
                  className={`flex items-center gap-2 w-full text-left px-3 py-1.5 text-[11px] transition-colors ${
                    isSimulating
                      ? light
                        ? "text-red-700 bg-red-50 hover:bg-red-100"
                        : "text-red-300 bg-red-500/10 hover:bg-red-500/20"
                      : light
                        ? "text-gray-700 hover:bg-gray-50"
                        : "text-slate-200 hover:bg-white/[0.06]"
                  }`}
                >
                  <Zap className="w-3.5 h-3.5 opacity-80" aria-hidden="true" />
                  {isSimulating ? "Stop simulation" : "Simulate failures"}
                </button>
                <button
                  role="menuitem"
                  type="button"
                  onClick={() => {
                    setActionsOpen(false);
                    generateReport();
                  }}
                  className={`flex items-center gap-2 w-full text-left px-3 py-1.5 text-[11px] transition-colors ${
                    light
                      ? "text-gray-700 hover:bg-gray-50"
                      : "text-slate-200 hover:bg-white/[0.06]"
                  }`}
                >
                  <FileText className="w-3.5 h-3.5 opacity-80" aria-hidden="true" />
                  Generate report
                </button>
                <button
                  role="menuitem"
                  type="button"
                  onClick={() => {
                    setActionsOpen(false);
                    exportTopologyImage();
                  }}
                  className={`flex items-center gap-2 w-full text-left px-3 py-1.5 text-[11px] transition-colors ${
                    light
                      ? "text-gray-700 hover:bg-gray-50"
                      : "text-slate-200 hover:bg-white/[0.06]"
                  }`}
                >
                  <ImageIcon className="w-3.5 h-3.5 opacity-80" aria-hidden="true" />
                  Export topology as PNG
                </button>
              </div>
            )}
          </div>
          <MaintenanceCalendar
            iconBtnClass={(active = false) =>
              `flex items-center justify-center w-8 h-8 rounded-lg transition-all ${
                active
                  ? light
                    ? "bg-gray-200/80 text-gray-700"
                    : "bg-slate-700 text-slate-200"
                  : light
                    ? "text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                    : "text-slate-400 hover:bg-white/5 hover:text-slate-200"
              }`
            }
          />
        </div>
      </div>

      {/* Simulation impact banner */}
      {isSimulating && (
        <div
          className={`flex items-center justify-center gap-2.5 px-4 py-2 text-[11px] leading-relaxed border-b ${
            impactSummary
              ? "bg-red-950/60 border-red-800/40 text-red-200"
              : light
                ? "bg-amber-50 border-amber-200 text-amber-700"
                : "bg-amber-900/15 border-amber-800/20 text-amber-300/90"
          }`}
        >
          <Zap className="w-3.5 h-3.5 flex-shrink-0" aria-hidden="true" />
          {impactSummary ? (
            <span>
              <strong>
                {impactSummary.failedNodes} node{impactSummary.failedNodes !== 1 ? "s" : ""}
              </strong>
              {impactSummary.failedLinks > 0 && (
                <>
                  {", "}
                  <strong>
                    {impactSummary.failedLinks} link
                    {impactSummary.failedLinks !== 1 ? "s" : ""}
                  </strong>
                </>
              )}{" "}
              failed — <strong>{impactSummary.downEdges}</strong> of {impactSummary.totalEdges}{" "}
              paths down,{" "}
              <strong className="text-green-400">{impactSummary.upEdges} surviving</strong>
            </span>
          ) : (
            <span>Click on zones, nodes, or edges to simulate failures</span>
          )}
        </div>
      )}
    </>
  );
}
