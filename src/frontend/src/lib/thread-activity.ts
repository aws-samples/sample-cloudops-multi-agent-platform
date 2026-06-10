/**
 * Hook for per-thread activity polling.
 *
 * Surfaces the server-side "is the backend still working on this thread"
 * state so the UI can show a "still running" card + disable the composer
 * when you navigate back to a thread whose invocation began in another
 * tab. Polls every 2s while ``status === "running"``; stops once the
 * thread goes idle or errors.
 */

import { useEffect, useRef, useState } from "react";
import { getThreadActivity, type ThreadActivity } from "./runtime-client";
import { getToken } from "./auth";

const POLL_INTERVAL_MS = 2000;
const IDLE: ThreadActivity = { status: "idle" };

export function useThreadActivity(threadId: string | null): ThreadActivity {
    const [activity, setActivity] = useState<ThreadActivity>(IDLE);
    const abortedRef = useRef(false);

    useEffect(() => {
        abortedRef.current = false;
        if (!threadId) {
            setActivity(IDLE);
            return;
        }

        let timer: ReturnType<typeof setTimeout> | null = null;

        const tick = async () => {
            if (abortedRef.current) return;
            try {
                const next = await getThreadActivity(threadId, getToken);
                if (abortedRef.current) return;
                setActivity(next);
                if (next.status === "running") {
                    timer = setTimeout(tick, POLL_INTERVAL_MS);
                }
            } catch {
                if (abortedRef.current) return;
                // On fetch error, stop polling but don't clobber existing state.
                setActivity((prev) => (prev.status === "running" ? { status: "idle" } : prev));
            }
        };

        void tick();

        return () => {
            abortedRef.current = true;
            if (timer) clearTimeout(timer);
        };
    }, [threadId]);

    return activity;
}
