/**
 * Visualizer-state extractor — shared between the live runtime and the
 * history loader.
 *
 * Memory stores the assistant's enriched text with `<tool>...</tool>` tags
 * wrapping each tool call's raw output. On live stream, `MyRuntimeProvider`
 * intercepts `TOOL_CALL_RESULT` events and synthesises a
 * `<visualizer-state>` segment. On history reload, `page.tsx` rebuilds the
 * same synthesis by walking saved `<tool>` tags through this extractor —
 * without it, returning to a prior DX thread would show the assistant's
 * prose but no VisualizerCard (the tag only ever existed in the live
 * stream's synthetic segment, never in memory).
 */

/** MCP tool names whose output contains visualizer-ready data. */
export const VIZ_MCP_TOOL_NAMES = new Set([
  "discover_dx_topology",
  "assess_dx_resiliency",
]);

/**
 * Agent delegate names whose `tool_trace` may contain a visualizer-relevant
 * sub-call. Supervisor routes through `ops-excellence-agent` (orchestrator)
 * which routes to `network-resiliency-agent` (leaf). Either level may appear
 * as the top-level tool name depending on where the chain started — walk both.
 */
export const VIZ_AGENT_NAMES = new Set([
  "ops-excellence-agent",
  "network-resiliency-agent",
]);

export interface VisualizerState {
  topology?: unknown;
  assessment?: unknown;
  toolName: string;
}

/** Recursively decode up to 3 JSON-escape layers; stops when non-string. */
function peelJsonLayers(raw: unknown): unknown {
  let parsed: unknown = raw;
  for (let i = 0; i < 3; i++) {
    if (typeof parsed !== "string") break;
    try {
      parsed = JSON.parse(parsed);
    } catch {
      break;
    }
  }
  return parsed;
}

/** Pull topology/assessment out of an MCP tool's raw output blob. */
function extractFromMcpOutput(
  toolName: string,
  resultContent: unknown,
): VisualizerState | null {
  if (!VIZ_MCP_TOOL_NAMES.has(toolName)) return null;
  let parsed = peelJsonLayers(resultContent);
  if (typeof parsed !== "object" || parsed === null) return null;

  // Agent wrapper shape: { response: "...", tool_trace: [...] }. If `response`
  // is a JSON string, peel it to get at the actual data.
  if (
    "response" in parsed &&
    typeof (parsed as Record<string, unknown>).response === "string"
  ) {
    const inner = peelJsonLayers((parsed as Record<string, unknown>).response);
    if (typeof inner === "object" && inner !== null) parsed = inner;
  }

  const obj = parsed as Record<string, unknown>;
  // MCP handler shape: { status: "success", data: {...} } — unwrap `data`.
  const d = (obj.data && typeof obj.data === "object" ? obj.data : obj) as Record<
    string,
    unknown
  >;

  if (toolName === "discover_dx_topology") {
    if (
      Array.isArray(d.connections) ||
      Array.isArray(d.virtualInterfaces) ||
      Array.isArray(d.dxGateways)
    ) {
      return { topology: d, toolName };
    }
    return null;
  }
  if (toolName === "assess_dx_resiliency") {
    // New paired shape: { topology: {...}, assessment: {...} }
    if (
      d.topology &&
      typeof d.topology === "object" &&
      d.assessment &&
      typeof d.assessment === "object"
    ) {
      return {
        topology: d.topology,
        assessment: d.assessment,
        toolName,
      };
    }
    // Legacy assessment-only shape (back-compat with older deployments).
    if ("perDxGateway" in d || "resiliency" in d) {
      return { assessment: d, toolName };
    }
    return null;
  }
  return null;
}

/**
 * Pull a visualizer-ready payload out of an agent-delegate tool call by
 * recursing through the whole payload looking for MCP tool names we care
 * about. Handles all the shapes the supervisor's response can take:
 *
 *   • ``{response, tool_trace: [...]}`` — the canonical agent-delegate shape.
 *   • Nested string JSON — ``output`` may itself be a JSON-encoded
 *     ``{response, tool_trace}`` (common when the leaf runs a sub-delegate
 *     and the orchestrator forwards the inner trace as a string).
 *   • Inline ``<tool>...</tool>`` tags embedded inside ``response`` text —
 *     the supervisor interleaves enriched tool records into the streamed
 *     text; scanning those as a fallback recovers the payload even when
 *     the wire-level ``tool_trace`` isn't populated (observed for some
 *     live AG-UI envelopes where the top-level object was missing the
 *     ``tool_trace`` key entirely).
 *
 * Looser than the old walker: recurse every object/array value, not just
 * ``tool_trace``, so nested traces in unexpected keys still surface.
 */
function extractFromAgentDelegate(
  toolName: string,
  resultContent: unknown,
): VisualizerState | null {
  if (!VIZ_AGENT_NAMES.has(toolName)) return null;

  const seen = new WeakSet<object>();
  const acc: { topology?: unknown; assessment?: unknown } = {};

  const tryMatchMcp = (obj: Record<string, unknown>) => {
    const rawName = typeof obj.tool_name === "string" ? obj.tool_name : "";
    const bareName = rawName.includes("___") ? rawName.split("___").pop()! : rawName;
    if (bareName && VIZ_MCP_TOOL_NAMES.has(bareName)) {
      const mcp = extractFromMcpOutput(bareName, obj.output);
      if (mcp?.topology && !acc.topology) acc.topology = mcp.topology;
      if (mcp?.assessment && !acc.assessment) acc.assessment = mcp.assessment;
    }
  };

  const walk = (node: unknown): void => {
    const parsed = peelJsonLayers(node);
    if (parsed === null || parsed === undefined) return;

    if (typeof parsed === "string") {
      // A raw string that didn't parse as JSON — scan for <tool> tags the
      // supervisor embeds in enriched responses, and recover via the same
      // extractor the history path uses.
      if (parsed.includes("<tool>") && (!acc.topology || !acc.assessment)) {
        const fromMemory = extractVisualizerStateFromMemory(parsed);
        if (fromMemory) {
          if (fromMemory.topology && !acc.topology) acc.topology = fromMemory.topology;
          if (fromMemory.assessment && !acc.assessment) acc.assessment = fromMemory.assessment;
        }
      }
      return;
    }

    if (typeof parsed !== "object") return;
    if (seen.has(parsed as object)) return;
    seen.add(parsed as object);

    if (Array.isArray(parsed)) {
      for (const child of parsed) walk(child);
      return;
    }

    const obj = parsed as Record<string, unknown>;
    tryMatchMcp(obj);

    // Recurse into every value. peelJsonLayers handles string-encoded JSON.
    for (const v of Object.values(obj)) walk(v);
  };

  walk(resultContent);
  if (acc.topology || acc.assessment) {
    return { ...acc, toolName };
  }
  return null;
}

/**
 * Extract a visualizer-ready payload from a single tool result. Used by the
 * live AG-UI streamer on every `TOOL_CALL_RESULT` event.
 */
export function extractVisualizerState(
  toolName: string,
  resultContent: unknown,
): VisualizerState | null {
  if (VIZ_MCP_TOOL_NAMES.has(toolName)) {
    return extractFromMcpOutput(toolName, resultContent);
  }
  if (VIZ_AGENT_NAMES.has(toolName)) {
    return extractFromAgentDelegate(toolName, resultContent);
  }
  return null;
}

/**
 * Scan a saved memory blob for `<tool>...</tool>` segments and extract any
 * visualizer-relevant payloads. Returns one merged `VisualizerState` per
 * message (topology + assessment may come from separate tool calls).
 *
 * Saved format: `<tool>{"name": "...", "input": {...}, "output": "..."}</tool>`
 * The `name` inside the JSON matches what the live runtime's `tc.name` is,
 * so the same extractor applies unchanged.
 */
export function extractVisualizerStateFromMemory(
  enrichedText: string,
): VisualizerState | null {
  if (!enrichedText.includes("<tool>")) return null;
  const toolRe = /<tool>([\s\S]*?)<\/tool>/g;
  const merged: { topology?: unknown; assessment?: unknown; toolName: string } = {
    toolName: "",
  };
  let match: RegExpExecArray | null;
  while ((match = toolRe.exec(enrichedText)) !== null) {
    let toolJson: unknown;
    try {
      toolJson = JSON.parse(match[1]);
    } catch {
      continue;
    }
    if (typeof toolJson !== "object" || toolJson === null) continue;
    const toolObj = toolJson as Record<string, unknown>;
    // Chat-mode saves use `name`, report-mode section traces use `tool_name`.
    // Accept either so both save paths are recoverable.
    const rawName =
      (typeof toolObj.name === "string" ? toolObj.name : "") ||
      (typeof toolObj.tool_name === "string" ? toolObj.tool_name : "");
    if (!rawName) continue;
    // Tool names may come through with the AgentCore gateway prefix
    // (`network-resilience___discover_dx_topology`) — strip it before matching.
    const name = rawName.includes("___") ? rawName.split("___").pop()! : rawName;
    // Agent-delegate tool tags store `tool_trace` alongside `output` at the
    // top level of the saved JSON — so we pass the full `toolObj` to the
    // agent-delegate walker so it can recurse through nested traces.
    // MCP tool tags store the payload inside `output`, so for those we keep
    // the old behavior of passing `output` through.
    const isAgentDelegate = VIZ_AGENT_NAMES.has(name);
    const payload = isAgentDelegate
      ? toolObj
      : (toolObj.output ?? toolObj.response ?? toolObj);
    const state = extractVisualizerState(name, payload);
    if (!state) continue;
    if (state.topology && !merged.topology) merged.topology = state.topology;
    if (state.assessment && !merged.assessment) merged.assessment = state.assessment;
    merged.toolName = state.toolName;
    if (merged.topology && merged.assessment) break;
  }
  if (!merged.topology && !merged.assessment) return null;
  return merged;
}
