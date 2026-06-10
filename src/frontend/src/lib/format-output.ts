/**
 * Tool-output formatting for the trace + artifact panels.
 *
 * The wire carries full tool output at full fidelity so downstream consumers
 * (follow-up-turn LLMs re-reading memory, the visualizer-state extractor,
 * any future data widget) can re-parse structured JSON. Backend-side
 * truncation was removed in favor of abbreviating at render time — which
 * is what the user actually experiences.
 *
 * Policy:
 *   • `formatOutput` always returns the full pretty-printed text.
 *   • `abbreviateByLines` returns a compact view (head + tail lines) plus
 *     a `truncated` flag so the UI can offer a "show all" toggle. Keep
 *     this the ONLY place the line budget lives so TracePanel and
 *     ReportPanel stay consistent.
 */

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

/** Pretty-print a tool output blob; unescapes nested JSON first. */
export function formatOutput(text: string): string {
  if (!text) return "";
  const parsed = peelJsonLayers(text);
  if (typeof parsed === "object" && parsed !== null) {
    return JSON.stringify(parsed, null, 2);
  }
  return typeof parsed === "string" ? parsed : String(parsed);
}

export interface AbbreviatedOutput {
  /** The condensed text actually shown when collapsed. */
  abbreviated: string;
  /** True if we dropped any lines (caller should offer an "expand" toggle). */
  truncated: boolean;
  /** Total line count of the un-abbreviated text. */
  totalLines: number;
}

const DEFAULT_HEAD_LINES = 20;
const DEFAULT_TAIL_LINES = 10;

/**
 * Show the first `headLines` and last `tailLines` of `text`, with a
 * single divider row noting how many lines were hidden. Preserves JSON
 * readability far better than a char-count cut because the hidden
 * middle rows are typically the bulk of an array body.
 */
export function abbreviateByLines(
  text: string,
  headLines: number = DEFAULT_HEAD_LINES,
  tailLines: number = DEFAULT_TAIL_LINES,
): AbbreviatedOutput {
  const lines = text.split("\n");
  const total = lines.length;
  if (total <= headLines + tailLines + 1) {
    return { abbreviated: text, truncated: false, totalLines: total };
  }
  const hidden = total - headLines - tailLines;
  const head = lines.slice(0, headLines);
  const tail = lines.slice(total - tailLines);
  const abbreviated = [
    ...head,
    `… ${hidden} more line${hidden === 1 ? "" : "s"} hidden — click "Show all" to expand …`,
    ...tail,
  ].join("\n");
  return { abbreviated, truncated: true, totalLines: total };
}
