"use client";

import { createContext, useContext, type ReactNode } from "react";

/**
 * Cross-component flag that marks a thread as busy **on the server**
 * (i.e. the backend is still running an invocation for this thread,
 * regardless of whether this tab is the one streaming it). Populated
 * in ``src/app/page.tsx`` via ``useThreadActivity``; consumed by the
 * composer in ``src/components/Thread.tsx`` to disable input and by
 * ``src/app/page.tsx`` itself to swap the chat area for a status card.
 *
 * Distinct from AssistantUI's ``threadRuntime.isRunning`` — that flag
 * tracks THIS tab's local stream only.
 */
const ThreadBusyContext = createContext<boolean>(false);

export function ThreadBusyProvider({
    busy,
    children,
}: {
    busy: boolean;
    children: ReactNode;
}) {
    return <ThreadBusyContext.Provider value={busy}>{children}</ThreadBusyContext.Provider>;
}

export function useThreadBusyRemote(): boolean {
    return useContext(ThreadBusyContext);
}
