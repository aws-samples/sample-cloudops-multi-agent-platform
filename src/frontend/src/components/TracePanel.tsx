"use client";

import { useCallback, useRef, useState } from "react";
import {
    X,
    Wrench,
    ChevronDown,
    ChevronRight,
    Brain,
    Loader2,
    Image as ImageIcon,
} from "lucide-react";
import { useFocusTrap } from "@/lib/a11y/use-focus-trap";
import { useExportImage, suggestImageFilename } from "@/lib/export/use-export-image";
import { formatOutput, abbreviateByLines } from "@/lib/format-output";

interface TraceEntry {
    tool_name: string;
    duration_s?: number;
    status?: string;
    input?: Record<string, unknown>;
    output?: string;
    tool_trace?: TraceEntry[];
}

interface TraceData {
    thinking?: string;
    tools: Array<{
        name: string;
        input: Record<string, unknown>;
        output?: string;
        tool_trace?: TraceEntry[];
        isStreaming?: boolean;
    }>;
    isStreaming: boolean;
}

interface TracePanelProps {
    trace: TraceData;
    onClose: () => void;
}

export function TracePanel({ trace, onClose }: TracePanelProps) {
    const scrollRef = useRef<HTMLDivElement>(null);
    const contentRef = useRef<HTMLDivElement>(null);
    const panelRef = useFocusTrap<HTMLDivElement>(true, onClose);
    const exportImage = useExportImage();

    const handleDownloadImage = useCallback(async () => {
        await exportImage({
            element: contentRef.current,
            filename: suggestImageFilename("agent-trace"),
        });
    }, [exportImage]);

    return (
        <div
            ref={panelRef}
            className="flex flex-col h-full"
            style={{
                background: "var(--bg-primary)",
                borderLeft: "1px solid var(--border-subtle)",
                width: "480px",
                minWidth: "380px",
            }}
        >
            {/* Header */}
            <div
                className="flex items-center justify-between px-4 py-3 shrink-0"
                style={{ borderBottom: "1px solid var(--border-subtle)" }}
            >
                <div className="flex items-center gap-2">
                    <Brain className="h-4 w-4" style={{ color: "var(--accent)" }} />
                    <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                        {trace.isStreaming ? "Agent Thinking..." : "Agent Trace"}
                    </span>
                    {trace.isStreaming && (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" style={{ color: "var(--accent)" }} />
                    )}
                </div>
                <div className="flex items-center gap-1">
                    {(trace.thinking || trace.tools.length > 0) && !trace.isStreaming && (
                        <button
                            onClick={handleDownloadImage}
                            className="p-1 rounded-md hover:bg-[var(--bg-elevated)]"
                            aria-label="Download trace as PNG"
                            title="Download as PNG"
                        >
                            <ImageIcon className="h-4 w-4" style={{ color: "var(--text-muted)" }} />
                        </button>
                    )}
                    <button
                        onClick={onClose}
                        className="p-1 rounded-md hover:bg-[var(--bg-elevated)]"
                        aria-label="Close trace panel"
                    >
                        <X className="h-4 w-4" style={{ color: "var(--text-muted)" }} />
                    </button>
                </div>
            </div>

            {/* Content */}
            <div
                ref={(el) => {
                    scrollRef.current = el;
                    contentRef.current = el;
                }}
                className="flex-1 overflow-y-auto p-4 space-y-4"
            >
                {/* Thinking section */}
                {trace.thinking && (
                    <div>
                        <div className="flex items-center gap-1.5 mb-2">
                            <Brain className="h-3.5 w-3.5" style={{ color: "var(--text-muted)" }} />
                            <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
                                Reasoning
                            </span>
                        </div>
                        <div
                            className="rounded-lg p-3 text-xs leading-relaxed"
                            style={{
                                background: "var(--bg-elevated)",
                                color: "var(--text-secondary)",
                                maxHeight: "200px",
                                overflowY: "auto",
                            }}
                        >
                            {trace.thinking}
                        </div>
                    </div>
                )}

                {/* Tool calls */}
                {trace.tools.length > 0 && (
                    <div>
                        <div className="flex items-center gap-1.5 mb-2">
                            <Wrench className="h-3.5 w-3.5" style={{ color: "var(--text-muted)" }} />
                            <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
                                Tool Calls ({trace.tools.length})
                            </span>
                        </div>
                        <div className="space-y-2">
                            {trace.tools.map((tool, i) => (
                                <TraceToolCard key={i} tool={tool} />
                            ))}
                        </div>
                    </div>
                )}

                {/* Empty state */}
                {!trace.thinking && trace.tools.length === 0 && trace.isStreaming && (
                    <div className="flex flex-col items-center justify-center py-12 text-center">
                        <Loader2 className="h-8 w-8 animate-spin mb-3" style={{ color: "var(--accent)" }} />
                        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
                            Waiting for agent activity...
                        </p>
                    </div>
                )}
            </div>
        </div>
    );
}

/* ── Tool card in the trace panel ── */
function TraceToolCard({ tool }: { tool: TraceData["tools"][0] }) {
    const [open, setOpen] = useState(false);
    const hasInput = tool.input && Object.keys(tool.input).length > 0;

    // Parse output — extract clean response from _delegate wrapper if needed
    let cleanOutput = tool.output || "";
    let parsedTrace: TraceEntry[] = tool.tool_trace || [];
    if (cleanOutput) {
        let parsed: unknown = cleanOutput;
        for (let i = 0; i < 3; i++) {
            if (typeof parsed !== "string") break;
            try { parsed = JSON.parse(parsed); } catch { break; }
        }
        if (typeof parsed === "object" && parsed !== null) {
            const obj = parsed as Record<string, unknown>;
            const data = (obj.data || obj.response || obj.output || "") as string | Record<string, unknown>;
            if (typeof data === "string" && data.startsWith("{")) {
                try {
                    const inner = JSON.parse(data) as Record<string, unknown>;
                    cleanOutput = (inner.response || data) as string;
                    if (Array.isArray(inner.tool_trace) && !parsedTrace.length) parsedTrace = inner.tool_trace as TraceEntry[];
                } catch { cleanOutput = data; }
            } else if (typeof data === "string") {
                cleanOutput = data;
            }
            if (Array.isArray(obj.tool_trace) && !parsedTrace.length) parsedTrace = obj.tool_trace as TraceEntry[];
        }
    }
    const hasOutput = !!cleanOutput && cleanOutput !== tool.output; // Only show if we extracted something cleaner
    const hasNested = parsedTrace.length > 0;

    // If we couldn't parse, show raw output
    const displayOutput = hasOutput ? cleanOutput : (tool.output || "");
    const showOutput = !!displayOutput;

    return (
        <div
            className="rounded-lg overflow-hidden text-xs"
            style={{ border: "1px solid var(--accent-border-subtle)", background: "var(--accent-bg-faint)" }}
        >
            <button
                onClick={() => setOpen(!open)}
                className="flex items-center gap-2 w-full px-3 py-2 text-left"
                style={{ color: "var(--text-muted)" }}
                aria-expanded={open}
            >
                {tool.isStreaming ? (
                    <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" style={{ color: "var(--accent)" }} />
                ) : (
                    <Wrench className="h-3.5 w-3.5 shrink-0" style={{ color: "var(--accent)" }} />
                )}
                <span className="font-medium" style={{ color: "var(--accent)" }}>
                    {tool.isStreaming ? `Calling ${tool.name}…` : tool.name}
                </span>
                {hasNested && (
                    <span className="text-[10px] opacity-50">
                        ({parsedTrace.length} sub-call{parsedTrace.length > 1 ? "s" : ""})
                    </span>
                )}
                {open ? <ChevronDown className="h-3 w-3 ml-auto" /> : <ChevronRight className="h-3 w-3 ml-auto" />}
            </button>
            {open && (
                <div className="px-3 pb-3 space-y-2">
                    {hasInput && (
                        <div>
                            <div className="text-[10px] font-medium uppercase tracking-wider mb-1" style={{ color: "var(--text-muted)", opacity: 0.7 }}>Input</div>
                            <pre className="overflow-x-auto text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
                                {JSON.stringify(tool.input, null, 2)}
                            </pre>
                        </div>
                    )}
                    {hasNested && (
                        <div>
                            <div className="text-[10px] font-medium uppercase tracking-wider mb-1" style={{ color: "var(--text-muted)", opacity: 0.7 }}>Sub-agent calls</div>
                            <div className="space-y-1">
                                {parsedTrace.map((nested, i) => (
                                    <NestedTraceItem key={i} trace={nested} depth={0} />
                                ))}
                            </div>
                        </div>
                    )}
                    {showOutput && <OutputBlock text={displayOutput} />}
                    {!hasInput && !showOutput && !hasNested && tool.isStreaming && (
                        <div className="flex items-center gap-2 text-xs py-1" style={{ color: "var(--text-muted)" }}>
                            <Loader2 className="h-3 w-3 animate-spin" />
                            Running…
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

/* ── Nested trace item ── */
function NestedTraceItem({ trace, depth }: { trace: TraceEntry; depth: number }) {
    const [open, setOpen] = useState(false);
    const statusIcon = trace.status === "success" ? "✓" : trace.status === "started" ? "⏳" : "✗";
    const statusColor = trace.status === "success" ? "var(--accent)" : trace.status === "started" ? "var(--text-muted)" : "#ef4444";
    const duration = trace.duration_s != null ? `${trace.duration_s}s` : "";

    // Parse input — might be a JSON string from the tracing handler
    let parsedInput: Record<string, unknown> | null = null;
    if (trace.input) {
        if (typeof trace.input === "string") {
            try { parsedInput = JSON.parse(trace.input); } catch { parsedInput = { raw: trace.input }; }
        } else if (typeof trace.input === "object" && Object.keys(trace.input).length > 0) {
            parsedInput = trace.input;
        }
    }

    // Parse output — might need unescaping
    let displayOutput = trace.output || "";
    if (displayOutput) {
        // Try to extract clean text from _delegate wrapper
        try {
            const parsed = JSON.parse(displayOutput);
            if (typeof parsed === "object" && parsed !== null) {
                displayOutput = (parsed as Record<string, unknown>).response as string
                    || (parsed as Record<string, unknown>).data as string
                    || displayOutput;
            } else if (typeof parsed === "string") {
                displayOutput = parsed;
            }
        } catch { /* keep as-is */ }
    }

    const hasContent = !!parsedInput || !!displayOutput || (trace.tool_trace && trace.tool_trace.length > 0);

    return (
        <div
            className="rounded-md overflow-hidden text-xs"
            style={{ border: "1px solid var(--accent-border-subtle)", background: "var(--accent-bg-faint)", marginTop: "4px" }}
        >
            <button
                onClick={() => hasContent && setOpen(!open)}
                className="flex items-center gap-1.5 w-full px-2.5 py-1.5 text-left"
                style={{ color: "var(--text-muted)", cursor: hasContent ? "pointer" : "default" }}
            >
                <span style={{ color: statusColor, fontSize: "10px" }}>{statusIcon}</span>
                <Wrench className="h-2.5 w-2.5 shrink-0" style={{ color: "var(--accent)", opacity: 0.6 }} />
                <span className="font-medium" style={{ color: "var(--accent)" }}>{trace.tool_name}</span>
                {duration && <span className="opacity-60">({duration})</span>}
                {hasContent && (open ? <ChevronDown className="h-2.5 w-2.5 ml-auto" /> : <ChevronRight className="h-2.5 w-2.5 ml-auto" />)}
            </button>
            {open && (
                <div className="px-2.5 pb-2 space-y-1.5">
                    {parsedInput && (
                        <div>
                            <div className="text-[10px] font-medium uppercase tracking-wider mb-0.5" style={{ color: "var(--text-muted)", opacity: 0.7 }}>Input</div>
                            <pre className="overflow-x-auto text-[11px]" style={{ color: "var(--text-muted)" }}>
                                {JSON.stringify(parsedInput, null, 2)}
                            </pre>
                        </div>
                    )}
                    {trace.tool_trace && trace.tool_trace.length > 0 && (
                        <div>
                            <div className="text-[10px] font-medium uppercase tracking-wider mb-0.5" style={{ color: "var(--text-muted)", opacity: 0.7 }}>Sub-calls</div>
                            {trace.tool_trace.map((nt, i) => (
                                <NestedTraceItem key={i} trace={nt} depth={depth + 1} />
                            ))}
                        </div>
                    )}
                    {displayOutput && <OutputBlock text={displayOutput} compact />}
                </div>
            )}
        </div>
    );
}

/* ── Output block with head/tail abbreviation + "Show all" toggle ──
 * The wire carries full tool output; this is the single place it gets
 * condensed for human reading. Users who want to verify numbers click
 * "Show all" to see the full payload inline.
 */
function OutputBlock({ text, compact }: { text: string; compact?: boolean }) {
    const [expanded, setExpanded] = useState(false);
    const formatted = formatOutput(text);
    const { abbreviated, truncated, totalLines } = abbreviateByLines(formatted);
    const shown = expanded ? formatted : abbreviated;
    const fontClass = compact ? "text-[11px]" : "text-xs";
    const maxHeight = compact ? "200px" : "300px";

    return (
        <div>
            <div
                className="flex items-center justify-between mb-1"
                style={{ color: "var(--text-muted)", opacity: 0.7 }}
            >
                <span className="text-[10px] font-medium uppercase tracking-wider">
                    Output {truncated && `(${totalLines} lines)`}
                </span>
                {truncated && (
                    <button
                        onClick={() => setExpanded((e) => !e)}
                        className="text-[10px] font-medium hover:underline"
                        style={{ color: "var(--accent)" }}
                    >
                        {expanded ? "Show less" : "Show all"}
                    </button>
                )}
            </div>
            <pre
                className={`overflow-x-auto ${fontClass} leading-relaxed whitespace-pre-wrap`}
                style={{ color: "var(--text-secondary)", maxHeight, overflowY: "auto" }}
            >
                {shown}
            </pre>
        </div>
    );
}
