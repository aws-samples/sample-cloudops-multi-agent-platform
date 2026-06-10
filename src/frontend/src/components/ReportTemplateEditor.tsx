"use client";

import { useState, useCallback, useRef } from "react";
import { X, Plus, Trash2, Link2, Unlink, Save, Loader2 } from "lucide-react";
import type { Template } from "@/lib/runtime-client";

interface Section {
    id: string;
    title: string;
    prompt: string;
}

interface ReportTemplateEditorProps {
    template?: Template | null;
    onSave: (data: {
        name: string;
        description: string;
        sections: Section[];
        dependencies: Record<string, string>;
    }) => Promise<void>;
    onClose: () => void;
}

let _stableKeyCounter = 0;

export function ReportTemplateEditor({ template, onSave, onClose }: ReportTemplateEditorProps) {
    const [name, setName] = useState(template?.name ?? "");
    const [description, setDescription] = useState(template?.description ?? "");
    const [sections, setSections] = useState<Section[]>(() => {
        if (template?.sections?.length) return template.sections.map((s) => ({ ...s }));
        return [{ id: "section_1", title: "", prompt: "" }];
    });
    const [dependencies, setDependencies] = useState<Record<string, string>>(() =>
        template?.dependencies ? { ...template.dependencies } : {},
    );
    const [saving, setSaving] = useState(false);
    const [editingDep, setEditingDep] = useState<string | null>(null);

    // Stable keys for React list rendering — never change once assigned
    const stableKeys = useRef<number[]>(sections.map(() => ++_stableKeyCounter));

    const addSection = useCallback(() => {
        setSections((prev) => [...prev, { id: "", title: "", prompt: "" }]);
        stableKeys.current.push(++_stableKeyCounter);
    }, []);

    const removeSection = useCallback((removeIdx: number) => {
        setSections((prev) => {
            const removed = prev[removeIdx];
            const next = prev.filter((_, i) => i !== removeIdx);
            setDependencies((d) => {
                const cleaned: Record<string, string> = {};
                for (const [k, v] of Object.entries(d)) {
                    if (k !== removed.id && v !== removed.id) cleaned[k] = v;
                }
                return cleaned;
            });
            return next;
        });
        stableKeys.current.splice(removeIdx, 1);
    }, []);

    const updateSection = useCallback((idx: number, field: keyof Section, value: string) => {
        setSections((prev) => {
            const next = [...prev];
            const old = next[idx];
            next[idx] = { ...old, [field]: value };
            if (field === "id" && old.id !== value) {
                setDependencies((d) => {
                    const cleaned: Record<string, string> = {};
                    for (const [k, v] of Object.entries(d)) {
                        cleaned[k === old.id ? value : k] = v === old.id ? value : v;
                    }
                    return cleaned;
                });
            }
            return next;
        });
    }, []);

    const toggleDependency = useCallback((sectionId: string, dependsOn: string) => {
        setDependencies((prev) => {
            if (prev[sectionId] === dependsOn) {
                const next = { ...prev };
                delete next[sectionId];
                return next;
            }
            return { ...prev, [sectionId]: dependsOn };
        });
        setEditingDep(null);
    }, []);

    const handleSave = useCallback(async () => {
        if (!name.trim() || sections.length === 0) return;
        if (saving) return; // prevent double-click
        for (const s of sections) {
            if (!s.id.trim() || !s.title.trim()) return;
        }
        setSaving(true);
        try {
            await onSave({ name, description, sections, dependencies });
        } finally {
            setSaving(false);
        }
    }, [name, description, sections, dependencies, onSave, saving]);

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.5)" }}>
            <div className="relative flex flex-col w-full max-w-3xl max-h-[90vh] rounded-xl overflow-hidden"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border-default)" }}>
                {/* Header */}
                <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                    <h2 className="text-base font-medium" style={{ color: "var(--text-primary)" }}>
                        {template ? "Edit Report Template" : "New Report Template"}
                    </h2>
                    <button onClick={onClose} className="p-1 rounded-md hover:bg-[var(--bg-surface)]" aria-label="Close">
                        <X className="h-4 w-4" style={{ color: "var(--text-muted)" }} />
                    </button>
                </div>

                {/* Body */}
                <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
                    <div className="space-y-3">
                        <div>
                            <label className="block text-xs font-medium mb-1" style={{ color: "var(--text-secondary)" }}>Template Name</label>
                            <input value={name} onChange={(e) => setName(e.target.value)}
                                placeholder="e.g. Monthly FinOps Report"
                                className="w-full px-3 py-2 rounded-lg text-sm"
                                style={{ background: "var(--bg-surface)", border: "1px solid var(--border-default)", color: "var(--text-primary)" }} />
                        </div>
                        <div>
                            <label className="block text-xs font-medium mb-1" style={{ color: "var(--text-secondary)" }}>Description</label>
                            <input value={description} onChange={(e) => setDescription(e.target.value)}
                                placeholder="Brief description"
                                className="w-full px-3 py-2 rounded-lg text-sm"
                                style={{ background: "var(--bg-surface)", border: "1px solid var(--border-default)", color: "var(--text-primary)" }} />
                        </div>
                    </div>

                    {/* Sections */}
                    <div>
                        <div className="flex items-center justify-between mb-3">
                            <label className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>Sections ({sections.length})</label>
                            <button onClick={addSection} className="flex items-center gap-1 text-xs px-2 py-1 rounded-md"
                                style={{ color: "var(--accent)", background: "var(--bg-surface)", border: "1px solid var(--border-default)" }}>
                                <Plus className="h-3 w-3" /> Add Section
                            </button>
                        </div>
                        <div className="space-y-3">
                            {sections.map((section, idx) => {
                                const dep = dependencies[section.id];
                                const depSection = dep ? sections.find((s) => s.id === dep) : null;
                                const availableDeps = sections.filter((s) => s.id !== section.id);
                                return (
                                    <div key={stableKeys.current[idx]} className="rounded-lg p-3 space-y-2"
                                        style={{ background: "var(--bg-surface)", border: "1px solid var(--border-subtle)" }}>
                                        <div className="flex items-center gap-2">
                                            <span className="text-xs font-mono px-1.5 py-0.5 rounded" style={{ background: "var(--bg-elevated)", color: "var(--text-muted)" }}>{idx + 1}</span>
                                            <input value={section.id} onChange={(e) => updateSection(idx, "id", e.target.value.replace(/\s/g, "_").toLowerCase())}
                                                placeholder="section_id"
                                                className="flex-1 px-2 py-1 rounded text-xs font-mono"
                                                style={{ background: "var(--bg-primary)", border: "1px solid var(--border-default)", color: "var(--text-secondary)" }} />
                                            {sections.length > 1 && (
                                                <button onClick={() => removeSection(idx)} className="p-1 rounded hover:bg-[var(--bg-elevated)]" aria-label="Remove section">
                                                    <Trash2 className="h-3.5 w-3.5" style={{ color: "var(--text-muted)" }} />
                                                </button>
                                            )}
                                        </div>
                                        <input value={section.title} onChange={(e) => updateSection(idx, "title", e.target.value)}
                                            placeholder="Section title"
                                            className="w-full px-2 py-1.5 rounded text-sm"
                                            style={{ background: "var(--bg-primary)", border: "1px solid var(--border-default)", color: "var(--text-primary)" }} />
                                        <textarea value={section.prompt} onChange={(e) => updateSection(idx, "prompt", e.target.value)}
                                            placeholder="Agent prompt. Use {month} and {year} as placeholders." rows={3}
                                            className="w-full px-2 py-1.5 rounded text-xs leading-relaxed resize-y"
                                            style={{ background: "var(--bg-primary)", border: "1px solid var(--border-default)", color: "var(--text-secondary)", minHeight: 60 }} />
                                        {/* Dependency */}
                                        <div className="flex items-center gap-2">
                                            {dep && depSection ? (
                                                <span className="flex items-center gap-1 text-xs px-2 py-0.5 rounded-full" style={{ background: "var(--bg-elevated)", color: "var(--accent)" }}>
                                                    <Link2 className="h-3 w-3" /> Depends on: {depSection.title || depSection.id}
                                                    <button onClick={() => setDependencies((d) => { const n = { ...d }; delete n[section.id]; return n; })} className="ml-1" aria-label="Remove dependency">
                                                        <X className="h-3 w-3" />
                                                    </button>
                                                </span>
                                            ) : availableDeps.length > 0 ? (
                                                editingDep === section.id ? (
                                                    <div className="flex items-center gap-1 flex-wrap">
                                                        <span className="text-xs" style={{ color: "var(--text-muted)" }}>Depends on:</span>
                                                        {availableDeps.map((d) => (
                                                            <button key={d.id} onClick={() => toggleDependency(section.id, d.id)}
                                                                className="text-xs px-2 py-0.5 rounded-full"
                                                                style={{ background: "var(--bg-elevated)", color: "var(--text-secondary)", border: "1px solid var(--border-default)" }}>
                                                                {d.title || d.id}
                                                            </button>
                                                        ))}
                                                        <button onClick={() => setEditingDep(null)} className="text-xs px-1" style={{ color: "var(--text-muted)" }}>Cancel</button>
                                                    </div>
                                                ) : (
                                                    <button onClick={() => setEditingDep(section.id)} className="flex items-center gap-1 text-xs px-2 py-0.5 rounded-full"
                                                        style={{ color: "var(--text-muted)", border: "1px dashed var(--border-default)" }}>
                                                        <Unlink className="h-3 w-3" /> Add dependency
                                                    </button>
                                                )
                                            ) : (
                                                <span className="text-xs" style={{ color: "var(--text-muted)" }}>Independent (runs in parallel)</span>
                                            )}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                </div>

                {/* Footer */}
                <div className="flex items-center justify-end gap-3 px-5 py-3" style={{ borderTop: "1px solid var(--border-subtle)" }}>
                    <button onClick={onClose} className="px-4 py-2 rounded-lg text-sm"
                        style={{ color: "var(--text-secondary)", border: "1px solid var(--border-default)" }}>
                        Cancel
                    </button>
                    <button onClick={handleSave} disabled={saving || !name.trim() || sections.length === 0}
                        className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium"
                        style={{ background: saving ? "var(--bg-surface)" : "var(--accent)", color: "white", opacity: saving || !name.trim() ? 0.5 : 1 }}>
                        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                        {template ? "Update" : "Create"}
                    </button>
                </div>
            </div>
        </div>
    );
}
