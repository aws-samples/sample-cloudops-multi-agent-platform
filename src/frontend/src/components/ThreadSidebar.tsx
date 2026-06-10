"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { Plus, Trash2, MessageSquare } from "lucide-react";
import { getToken, getActorId } from "@/lib/auth";

interface ThreadInfo {
  id: string;
  title: string;
  created_at: string;
}

interface ThreadSidebarProps {
  currentThreadId: string | null;
  onSelectThread: (id: string) => void;
  onNewThread: () => void;
  userId?: string;
}

type ThreadGroup = { label: string; threads: ThreadInfo[] };

function groupThreadsByDate(threads: ThreadInfo[]): ThreadGroup[] {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const minus7 = new Date(startOfToday.getTime() - 7 * 86400000);
  const minus30 = new Date(startOfToday.getTime() - 30 * 86400000);

  const buckets: Record<string, ThreadInfo[]> = {
    Today: [],
    "Previous 7 Days": [],
    "Previous 30 Days": [],
    Older: [],
  };

  for (const t of threads) {
    const d = new Date(t.created_at);
    if (d >= startOfToday) buckets["Today"].push(t);
    else if (d >= minus7) buckets["Previous 7 Days"].push(t);
    else if (d >= minus30) buckets["Previous 30 Days"].push(t);
    else buckets["Older"].push(t);
  }

  return ["Today", "Previous 7 Days", "Previous 30 Days", "Older"]
    .filter((label) => buckets[label].length > 0)
    .map((label) => ({ label, threads: buckets[label] }));
}

function SidebarSkeleton() {
  return (
    <div className="p-2 space-y-1" aria-label="Loading chats">
      {[0.92, 0.68, 0.85, 0.55, 0.75].map((w, i) => (
        <div key={i} className="flex items-center gap-2 px-3 py-2">
          <div className="skeleton h-3.5 w-3.5 rounded shrink-0" />
          <div className="skeleton h-3.5 rounded" style={{ width: `${w * 100}%` }} />
        </div>
      ))}
    </div>
  );
}

function optimisticKey(userId: string) { return `optimistic-threads:${userId}`; }

function loadOptimistic(userId: string): Map<string, ThreadInfo> {
  try {
    const raw = sessionStorage.getItem(optimisticKey(userId));
    if (!raw) return new Map();
    return new Map(JSON.parse(raw) as [string, ThreadInfo][]);
  } catch { return new Map(); }
}

function saveOptimistic(userId: string, map: Map<string, ThreadInfo>) {
  try {
    sessionStorage.setItem(optimisticKey(userId), JSON.stringify(Array.from(map.entries())));
  } catch { }
}

export function ThreadSidebar({ currentThreadId, onSelectThread, onNewThread, userId = "dev-user" }: ThreadSidebarProps) {
  const [threads, setThreads] = useState<ThreadInfo[]>([]);
  const [initialLoading, setInitialLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  // Track optimistic thread IDs so they survive backend fetches AND page refreshes
  const optimisticRef = useRef<Map<string, ThreadInfo>>(loadOptimistic(userId));

  const fetchThreads = useCallback(async () => {
    try {
      const { listSessions } = await import("@/lib/runtime-client");
      const token = await getToken();
      const actorId = getActorId();

      let sessions: Array<{ session_id: string; preview: string; created_at: string }> = [];
      try {
        sessions = await listSessions(actorId, () => Promise.resolve(token));
      } catch {
        sessions = [];
      }
      console.log("[ThreadSidebar] Parsed sessions:", sessions.length, "sessions for actorId:", actorId);
      const backendThreads: ThreadInfo[] = sessions.map((s: { session_id: string; preview: string; created_at: string }) => ({
        id: s.session_id,
        title: s.preview || s.session_id.slice(0, 12) + "...",
        created_at: s.created_at || new Date().toISOString(),
      }));
      const backendIds = new Set(backendThreads.map((t) => t.id));
      // Remove optimistic entries that the backend now knows about
      for (const id of optimisticRef.current.keys()) {
        if (backendIds.has(id)) optimisticRef.current.delete(id);
      }
      saveOptimistic(userId, optimisticRef.current);
      // Merge: backend threads + any remaining optimistic entries
      const merged = [
        ...Array.from(optimisticRef.current.values()),
        ...backendThreads,
      ];
      merged.sort((a, b) => b.created_at.localeCompare(a.created_at));
      setThreads(merged);
    } catch { } finally {
      setInitialLoading(false);
    }
  }, [userId]);

  useEffect(() => { fetchThreads(); }, [fetchThreads]);

  useEffect(() => {
    const interval = setInterval(fetchThreads, 30000);
    const onDone = () => { fetchThreads(); };
    const onStart = (e: Event) => {
      const { sessionId, prompt } = (e as CustomEvent).detail;
      setThreads((prev) => {
        if (prev.some((t) => t.id === sessionId)) return prev;
        const title = prompt.length > 60 ? prompt.slice(0, 60) + "..." : prompt;
        const entry = { id: sessionId, title, created_at: new Date().toISOString() };
        optimisticRef.current.set(sessionId, entry);
        saveOptimistic(userId, optimisticRef.current);
        return [entry, ...prev];
      });
    };
    window.addEventListener("chat-stream-done", onDone);
    window.addEventListener("chat-stream-start", onStart);
    return () => {
      clearInterval(interval);
      window.removeEventListener("chat-stream-done", onDone);
      window.removeEventListener("chat-stream-start", onStart);
    };
  }, [fetchThreads]);

  const deleteThread = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setDeletingId(id);
    try {
      const { deleteSession } = await import("@/lib/runtime-client");
      const token = await getToken();
      const actorId = getActorId();
      await deleteSession(id, actorId, () => Promise.resolve(token));
    } catch { }
    // API done — now trigger fade-out
    setDeletingId("fade:" + id);
    optimisticRef.current.delete(id);
    saveOptimistic(userId, optimisticRef.current);
    await new Promise(r => setTimeout(r, 300));
    setThreads((prev) => prev.filter((t) => t.id !== id));
    setDeletingId(null);
    if (currentThreadId === id) onNewThread();
  };

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="p-3" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
        <button
          onClick={onNewThread}
          className="flex items-center gap-2 w-full rounded-xl px-3 py-2 text-sm hover:bg-[var(--bg-elevated)] hover:text-[var(--text-primary)]"
          style={{ color: "var(--text-secondary)" }}
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          New Chat
        </button>
      </div>
      {initialLoading ? (
        <SidebarSkeleton />
      ) : (
        <nav className="flex-1 overflow-y-auto p-2" aria-label="Chat history">
          {threads.length === 0 ? (
            <div className="px-4 py-8 text-center text-xs" style={{ color: "var(--text-muted)" }}>
              Your conversations will appear here
            </div>
          ) : (
            groupThreadsByDate(threads).map((group) => (
              <div key={group.label}>
                <div
                  className="px-3 pt-3 pb-1 text-[11px] font-medium uppercase tracking-wider"
                  style={{ color: "var(--text-muted)" }}
                >
                  {group.label}
                </div>
                <div className="space-y-0.5">
                  {group.threads.map((t) => {
                    const active = currentThreadId === t.id;
                    const isDeleting = deletingId === t.id;
                    const isFading = deletingId === "fade:" + t.id;
                    const isDisabled = isDeleting || isFading;
                    return (
                      <div
                        key={t.id}
                        className="group relative"
                        style={{
                          opacity: isFading ? 0 : isDeleting ? 0.5 : 1,
                          transform: isFading ? "translateX(-20px)" : "translateX(0)",
                          transition: "opacity 0.3s ease, transform 0.3s ease",
                          pointerEvents: isDisabled ? "none" : "auto",
                        }}
                      >
                        <button
                          onClick={() => onSelectThread(t.id)}
                          className={`flex items-start gap-2 w-full rounded-xl px-3 py-2 text-sm text-left ${active
                            ? "bg-[var(--bg-elevated)] text-[var(--text-primary)]"
                            : "text-[var(--text-muted)] hover:bg-[var(--bg-surface)] hover:text-[var(--text-secondary)]"
                            }`}
                        >
                          <MessageSquare className="h-3.5 w-3.5 flex-shrink-0 opacity-60 mt-0.5" aria-hidden="true" />
                          <span className="flex-1 min-w-0 break-words pr-6">{t.title}</span>
                        </button>
                        <button
                          onClick={(e) => deleteThread(t.id, e)}
                          disabled={isDisabled}
                          className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded opacity-0 group-hover:opacity-40 hover:!opacity-100 disabled:opacity-100"
                          style={{ color: isDeleting ? "var(--text-muted)" : "var(--danger)" }}
                          aria-label={`Delete chat: ${t.title}`}
                        >
                          {isDeleting ? (
                            <div className="h-3.5 w-3.5 border-2 border-current border-t-transparent rounded-full animate-spin" />
                          ) : (
                            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                          )}
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))
          )}
        </nav>
      )}
    </div>
  );
}
