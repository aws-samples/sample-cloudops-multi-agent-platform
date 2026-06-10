# Visualizer

React Flow-based Direct Connect topology panel. Third right-side slideout mode alongside `TracePanel` and `ReportPanel`. Mounts when a chat message contains a `<visualizer-state>` tag (emitted when the agent calls `discover_dx_topology` or `assess_dx_resiliency`).

## Entry points

- `VisualizerPanel.tsx` — slideout shell. Owns width, focus trap, theme mirroring, and hooks (`useTopologyGraph`, `useReassess`, `useLiveStatus`). Dynamic-imports `FlowCanvas` with `ssr: false`.
- `VisualizerToolbar.tsx` — Simulate / Live Status / Recommendation toggles + view toggle + Generate Report button + maintenance calendar.
- `FlowCanvas.tsx` — React Flow instance with 19 node types + custom edge. Wrapped in `ReactFlowProvider` so `useExportTopologyImage` has its context.
- `ResiliencyScoreCard.tsx` — 1468-line port from source. Floats in bottom-left of canvas. Collapsed by default, expand/fullscreen on demand.

## Store: scope and gotchas

`src/lib/topology/store.ts` — Zustand store, visualizer-only. First Zustand store in the parent app; do NOT refactor other panels to use it.

Store fields track what source nodes expect: theme, simulation state, failed nodes/edges, hover path highlight, localStorage-backed user overrides (node sizes, edge rewires, hidden edges, user-added customer sites). All localStorage access is SSR-guarded — Next.js renders first paint server-side.

**Theme is mirrored from the parent `ThemeProvider`**, not owned. `VisualizerPanel` calls `setTheme(theme)` in an effect so node components' `useIsLight()` selector reflects the app-wide toggle.

## Ghost-node merging (viewMode=recommended)

`useTopologyGraph.ts` reads `assessment.perDxGateway[].recommendations[].additionalNodes` and merges them into `recommendedNodes`. The agent only populates ghosts for 2 of the 22 rules (single-location and single-connection-per-location); the remaining 20 rules return empty arrays. This matches source SPA behavior — don't assume every rule emits ghosts.

Phase 6 left a `collectGhosts()` helper that dedups by id across `perDxGateway[]` and `global.*`, and filters by `focusedDxGatewayId` when the user zooms to one gateway.

## Fast-path reassess

`useReassess.ts` watches `store.resiliencyTargets`. When the user flips a per-DXGW or bulk tier:
1. Debounce 120ms (swallows rapid clicks).
2. `POST /network-resilience/reassess` with cached topology + new targets.
3. Rewrite `store.assessment` with the response.
4. `useTopologyGraph` re-runs, ghost nodes + scorecard redraw.

**No chat turn fires.** This is the Phase 6 contract — target tier flips must be <500ms and never append to conversation history.

## Live status polling

`useLiveStatus.ts`. When `store.showLiveStatus` is true:
1. Group every VIF by region.
2. POST `/network-resilience/live-status` per region in parallel.
3. Merge counter values into `topologyData.bgpPrefixMetrics` (a Map).
4. Repeat every 60s until the toggle flips off or the panel unmounts.

The topology object gets shallow-replaced on every successful poll, so `useTopologyGraph`'s rebuild effect fires each time. If this ever becomes too churn-heavy, debounce the bgp metric merges separately.

## Why `FlowCanvas` is wrapped in `ReactFlowProvider`

React Flow's `<ReactFlow>` component creates its own internal Zustand provider, but `useReactFlow()` hooks called by *sibling* components (the PNG export button in the `<Controls>` panel) need a higher-level provider to attach to. Without the explicit `<ReactFlowProvider>` wrap, the hook throws "not used zustand provider as an ancestor" at first paint. `FlowCanvas` exports a thin wrapper that renders `<FlowCanvasInner />` inside `<ReactFlowProvider>`.

## Custom nodes must be `<div>`, not `<button>`

Direct lift from the source SPA's steering: React Flow's `<Handle>` component relies on a `<div>` parent to anchor edges correctly. Wrapping a custom node in `<button>` visually disconnects the edges. For keyboard accessibility, use `<div role="button" tabIndex={0} onKeyDown={...}>` instead. All 19 ported node files follow this rule.

## Map hydration from agent payload

The runtime serializes `Map` fields as plain objects (JSON has no native Map). `tgwRouteTables`, `cloudWanRoutes`, `bgpPrefixMetrics`, `regionNames` all need `new Map(Object.entries(...))` coercion before `buildGraph`/`CoreNetworkNode`/`TgwNode` can call `.get()` on them. `useTopologyGraph.rehydrateMaps()` does this idempotently — passing an already-Map value through is a no-op.

## Report generation

The toolbar's "Generate report" button dispatches a `generate-from-template` CustomEvent with `template_id: "dx_resiliency_report"`. `page.tsx` listens, flips the runtime into report mode, starts a fresh thread, and sends a prompt like "Generate the Direct Connect Resilience Review report". `agui_server.py` matches the template and runs its 6 sections via `generate_report_sections()`.

Report content arrives in `ReportPanel`, not the visualizer. The existing 3-way panel mutex in `page.tsx` closes the visualizer when the artifact panel opens. If that feels jarring, consider a "both open" mode — but the panel widths (960 + 480) already squeeze the chat column to ~240px on a 1440 display, so it's a deliberate trade-off.

## PNG export vs generic panel export

Two hooks in `src/lib/export/`:
- `use-export-image.ts` — generic DOM snapshot. Used by TracePanel and ReportPanel.
- `use-export-topology-image.ts` — specialized. Uses React Flow's `getNodesBounds` + `getViewportForBounds` to rasterize the *full* graph at ~4K resolution, not just the visible viewport. Used by the visualizer's Controls panel button.

Both set `skipFonts: true` to silence the cross-origin Google Fonts CSS access error (`html-to-image` can't read `cssRules` from external stylesheets). Exported images use the user's system font as fallback.

## Tour integration

`visualizer-tour.ts` registers a 6-step guided tour with the central `tour-registry`. `TourAutoStarter` mounted in `VisualizerPanel` waits for `data-tour="overlays"` to appear in the DOM, then fires driver.js with steps matching all known `data-tour` selectors. Re-runnable via the sidebar's "Show tour" menu.

## Known loose ends

- Recommendation engine duplication is documented in `temp/nr-migration/optimizations.md`. The TS recommendation engine was NOT ported — the Python path is authoritative, with the tradeoff that client-side "what if I flipped target to X" previews require a network round-trip to `/reassess` rather than local compute.
- `.selectable-text` and `.sim-canvas-frame` CSS classes referenced by node components and FlowCanvas aren't declared in `globals.css`. Visual gap during simulation frame rendering and text selection inside nodes. Not blocking.
- `MiniMap` uses React Flow defaults. Verify position at 960px panel width — adjust if it collides with the Legend panel on small screens.
