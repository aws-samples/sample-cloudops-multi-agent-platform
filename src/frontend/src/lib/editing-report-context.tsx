"use client";

import {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useState,
    type ReactNode,
} from "react";

/**
 * Tracks which existing report (if any) the user is currently editing
 * via the composer's Report-mode toggle. When set, the next message
 * sent forwards ``edit_report_id`` to the runtime so the backend routes
 * into the edit path (create a new versioned report from the parent).
 *
 * Cleared:
 * - when the user cancels via the composer chip,
 * - when a send completes (handled in MyRuntimeProvider — one edit per
 *   send, like report-mode's auto-untoggle).
 */
export interface EditingReport {
    report_id: string;
    title: string;
    version?: number;
}

interface EditingReportContextValue {
    editing: EditingReport | null;
    setEditing: (next: EditingReport | null) => void;
}

const EditingReportContext = createContext<EditingReportContextValue>({
    editing: null,
    setEditing: () => {},
});

export function EditingReportProvider({ children }: { children: ReactNode }) {
    const [editing, setEditingState] = useState<EditingReport | null>(null);

    const setEditing = useCallback((next: EditingReport | null) => {
        setEditingState(next);
        if (typeof window !== "undefined") {
            // Mirror onto window so the AG-UI payload builder in
            // MyRuntimeProvider can read it without threading React state.
            (window as Window & { __editingReportId?: string | null }).__editingReportId =
                next?.report_id ?? null;
        }
    }, []);

    // Clear the editing target once a send completes — matches how
    // report-mode auto-untoggles via the chat-stream-done event.
    useEffect(() => {
        const clear = () => setEditing(null);
        window.addEventListener("chat-stream-done", clear);
        return () => window.removeEventListener("chat-stream-done", clear);
    }, [setEditing]);

    return (
        <EditingReportContext.Provider value={{ editing, setEditing }}>
            {children}
        </EditingReportContext.Provider>
    );
}

export function useEditingReport() {
    return useContext(EditingReportContext);
}
