"use client";

/**
 * Sidebar button that opens a small popover listing registered tours.
 * Clicking a tour entry resets its seen-flag and starts it immediately — so
 * users can restart the visualizer tour (or any future feature tour) without
 * clearing localStorage by hand.
 *
 * Keeps itself invisible while no tours are registered so it doesn't
 * clutter the sidebar for users who haven't enabled any features yet.
 */

import { useEffect, useRef, useState } from "react";
import { HelpCircle } from "lucide-react";
import { listTours, resetTourSeen } from "./tour-registry";
import { useTour } from "./use-tour";

function TourItem({ id, label, onPicked }: { id: string; label: string; onPicked: () => void }) {
  const { start } = useTour(id);
  return (
    <button
      onClick={() => {
        resetTourSeen(id);
        start();
        onPicked();
      }}
      className="w-full flex items-center gap-2 text-left px-3 py-1.5 text-xs transition-colors hover:bg-[var(--bg-elevated)]"
      style={{ color: "var(--text-secondary)" }}
    >
      {label}
    </button>
  );
}

export function TourMenuButton() {
  const [open, setOpen] = useState(false);
  const [tourCount, setTourCount] = useState(0);
  const menuRef = useRef<HTMLDivElement>(null);

  // Recount on mount — tours register via side-effect imports from feature
  // components, so the registry might be empty until other panels render.
  // Poll until we see at least one registered tour or give up.
  useEffect(() => {
    const id = window.setInterval(() => {
      const count = listTours().length;
      setTourCount(count);
      if (count > 0) window.clearInterval(id);
    }, 500);
    // Initial sync read in case something's already registered.
    setTourCount(listTours().length);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  if (tourCount === 0) return null;
  const tours = listTours();

  return (
    <div ref={menuRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 w-full rounded-lg px-3 py-2 mb-2 text-xs font-medium transition-colors hover:bg-[var(--bg-elevated)]"
        style={{
          color: "var(--text-secondary)",
          background: "var(--bg-surface)",
          border: "1px solid var(--border-default)",
        }}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <HelpCircle className="h-3.5 w-3.5" aria-hidden="true" />
        Show tour
      </button>
      {open && (
        <div
          role="menu"
          className="absolute left-0 right-0 bottom-full mb-1 py-1 rounded-lg shadow-lg z-50"
          style={{
            background: "var(--bg-surface)",
            border: "1px solid var(--border-default)",
          }}
        >
          {tours.map((t) => (
            <TourItem key={t.id} id={t.id} label={t.label} onPicked={() => setOpen(false)} />
          ))}
        </div>
      )}
    </div>
  );
}
