"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { Plus, Pencil, Trash2, Play, X, Loader2, Layers } from "lucide-react";
import { listTemplates, deleteTemplate } from "@/lib/runtime-client";
import type { Template } from "@/lib/runtime-client";
import { getToken, getActorId } from "@/lib/auth";

interface ReportTemplateListProps {
    onClose: () => void;
    onEdit: (template: Template) => void;
    onNew: () => void;
    onGenerate: (template: Template, variables: Record<string, string>) => void;
}

/** Extract unique {placeholder} names from all section prompts in a template. */
function extractVariables(template: Template): string[] {
    const vars = new Set<string>();
    for (const s of template.sections ?? []) {
        const matches = s.prompt.matchAll(/\{(\w+)\}/g);
        for (const m of matches) vars.add(m[1]);
    }
    // Also check the legacy single prompt field
    if (template.prompt) {
        const matches = template.prompt.matchAll(/\{(\w+)\}/g);
        for (const m of matches) vars.add(m[1]);
    }
    return Array.from(vars).sort();
}

export function ReportTemplateList({ onClose, onEdit, onNew, onGenerate }: ReportTemplateListProps) {
    const [templates, setTemplates] = useState<Template[]>([]);
    const [loading, setLoading] = useState(true);
    const [expandedId, setExpandedId] = useState<string | null>(null);
    const [varValues, setVarValues] = useState<Record<string, string>>({});

    const fetchTemplates = useCallback(async () => {
        setLoading(true);
        try {
            const actorId = getActorId();
            const token = await getToken();
            const result = await listTemplates(actorId, async () => token);
            setTemplates(result);
        } catch (e) {
            console.error("Failed to load templates:", e);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { fetchTemplates(); }, [fetchTemplates]);

    const handleDelete = useCallback(async (t: Template) => {
        if (!confirm(`Delete "${t.name}"?`)) return;
        try {
            const token = await getToken();
            await deleteTemplate(t.user_id, t.template_id, async () => token);
            setTemplates((prev) => prev.filter((x) => x.template_id !== t.template_id));
        } catch (e) {
            console.error("Delete failed:", e);
        }
    }, []);

    const handleExpand = useCallback((templateId: string, template: Template) => {
        if (expandedId === templateId) {
            setExpandedId(null);
            return;
        }
        setExpandedId(templateId);
        // Pre-fill variable defaults
        const vars = extractVariables(template);
        const defaults: Record<string, string> = {};
        const now = new Date();
        const prevMonth = now.getMonth() === 0 ? 11 : now.getMonth() - 1;
        const prevYear = now.getMonth() === 0 ? now.getFullYear() - 1 : now.getFullYear();
        const monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
        for (const v of vars) {
            if (v === "month") defaults[v] = monthNames[prevMonth];
            else if (v === "year") defaults[v] = String(prevYear);
            else defaults[v] = "";
        }
        setVarValues(defaults);
    }, [expandedId]);

    const handleGenerate = useCallback((template: Template) => {
        onGenerate(template, varValues);
    }, [onGenerate, varValues]);

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.5)" }}>
            <div className="relative flex flex-col w-full max-w-2xl max-h-[85vh] rounded-xl overflow-hidden"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border-default)" }}>
                {/* Header */}
                <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                    <div className="flex items-center gap-2">
                        <Layers className="h-4 w-4" style={{ color: "var(--accent)" }} />
                        <h2 className="text-base font-medium" style={{ color: "var(--text-primary)" }}>Report Templates</h2>
                    </div>
                    <div className="flex items-center gap-2">
                        <button onClick={onNew} className="flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg font-medium"
                            style={{ background: "var(--accent)", color: "white" }}>
                            <Plus className="h-3 w-3" /> New Template
                        </button>
                        <button onClick={onClose} className="p-1 rounded-md hover:bg-[var(--bg-surface)]" aria-label="Close">
                            <X className="h-4 w-4" style={{ color: "var(--text-muted)" }} />
                        </button>
                    </div>
                </div>

                {/* Body */}
                <div className="flex-1 overflow-y-auto px-5 py-4">
                    {loading ? (
                        <div className="flex items-center justify-center py-12">
                            <Loader2 className="h-5 w-5 animate-spin" style={{ color: "var(--text-muted)" }} />
                        </div>
                    ) : templates.length === 0 ? (
                        <div className="text-center py-12">
                            <p className="text-sm" style={{ color: "var(--text-muted)" }}>No templates yet. Create one to get started.</p>
                        </div>
                    ) : (
                        <div className="space-y-2">
                            {templates.map((t) => {
                                const sectionCount = t.sections?.length ?? 0;
                                const depCount = t.dependencies ? Object.keys(t.dependencies).length : 0;
                                const isLegacy = !t.sections?.length && !!t.prompt;
                                const isExpanded = expandedId === t.template_id;
                                const vars = isExpanded ? extractVariables(t) : [];
                                return (
                                    <div key={t.template_id} className="rounded-lg p-3"
                                        style={{ background: "var(--bg-surface)", border: isExpanded ? "1px solid var(--accent-border, var(--border-default))" : "1px solid var(--border-subtle)" }}>
                                        <div className="flex items-start justify-between gap-3">
                                            <div className="flex-1 min-w-0">
                                                <div className="flex items-center gap-2">
                                                    <span className="text-sm font-medium truncate" style={{ color: "var(--text-primary)" }}>{t.name}</span>
                                                    {t.user_id === "system" && (
                                                        <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: "var(--bg-elevated)", color: "var(--text-muted)" }}>pre-loaded</span>
                                                    )}
                                                </div>
                                                {t.description && (
                                                    <p className="text-xs mt-0.5 truncate" style={{ color: "var(--text-muted)" }}>{t.description}</p>
                                                )}
                                                <div className="flex items-center gap-3 mt-1.5">
                                                    {sectionCount > 0 && <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>{sectionCount} sections</span>}
                                                    {depCount > 0 && <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>{depCount} dependencies</span>}
                                                    {isLegacy && <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>single-prompt</span>}
                                                </div>
                                            </div>
                                            <div className="flex items-center gap-1 shrink-0">
                                                {sectionCount > 0 && (
                                                    <button onClick={() => handleExpand(t.template_id, t)}
                                                        className="p-1.5 rounded-md transition-colors hover:bg-[var(--bg-elevated)]"
                                                        title="Generate report" aria-label="Generate report">
                                                        <Play className="h-3.5 w-3.5" style={{ color: "var(--accent)" }} />
                                                    </button>
                                                )}
                                                <button onClick={() => onEdit(t)}
                                                    className="p-1.5 rounded-md transition-colors hover:bg-[var(--bg-elevated)]"
                                                    title="Edit" aria-label="Edit template">
                                                    <Pencil className="h-3.5 w-3.5" style={{ color: "var(--text-muted)" }} />
                                                </button>
                                                <button onClick={() => handleDelete(t)}
                                                    className="p-1.5 rounded-md transition-colors hover:bg-[var(--bg-elevated)]"
                                                    title="Delete" aria-label="Delete template">
                                                    <Trash2 className="h-3.5 w-3.5" style={{ color: "var(--text-muted)" }} />
                                                </button>
                                            </div>
                                        </div>

                                        {/* Generate panel with dynamic variable inputs */}
                                        {isExpanded && (
                                            <div className="mt-3 pt-3 space-y-3" style={{ borderTop: "1px solid var(--border-subtle)" }}>
                                                {vars.length > 0 ? (
                                                    <div className="flex items-end gap-3 flex-wrap">
                                                        {vars.map((v) => (
                                                            <div key={v} className="flex flex-col gap-1">
                                                                <label className="text-[10px] font-medium capitalize" style={{ color: "var(--text-muted)" }}>{v.replace(/_/g, " ")}</label>
                                                                <input value={varValues[v] ?? ""} onChange={(e) => setVarValues((prev) => ({ ...prev, [v]: e.target.value }))}
                                                                    className="text-xs px-2 py-1.5 rounded w-32"
                                                                    style={{ background: "var(--bg-primary)", border: "1px solid var(--border-default)", color: "var(--text-primary)" }} />
                                                            </div>
                                                        ))}
                                                    </div>
                                                ) : (
                                                    <p className="text-xs" style={{ color: "var(--text-muted)" }}>No variables needed — report will generate with static prompts.</p>
                                                )}
                                                <button onClick={() => handleGenerate(t)}
                                                    className="flex items-center gap-1.5 text-xs px-4 py-1.5 rounded-lg font-medium"
                                                    style={{ background: "var(--accent)", color: "white" }}>
                                                    <Play className="h-3 w-3" /> Generate Report
                                                </button>
                                            </div>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
