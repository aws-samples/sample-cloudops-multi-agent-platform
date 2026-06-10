"use client";

/**
 * Central registry of guided tours.
 *
 * Each feature ships a tour file (e.g. `visualizer-tour.ts`) that describes
 * an ordered list of steps. Tours register themselves here so the
 * `TourProvider` can auto-start on first visit and so a "Show tour" menu
 * item can list all known tours.
 *
 * A tour is keyed by `tourId` — the localStorage key that tracks whether
 * the user has seen it. Bump the ID (e.g. append `-v2`) when the tour
 * content changes enough to warrant re-showing to returning users.
 */

import type { DriveStep } from "driver.js";

export interface TourDefinition {
  /** Unique ID — used as the localStorage key (`tour-seen-<id>`). */
  id: string;
  /** Human-readable name for the "Show tour" menu. */
  label: string;
  /** When true, the tour auto-starts on first mount. Defaults to true. */
  autoStart?: boolean;
  /** `driver.js` step list. */
  steps: DriveStep[];
}

const registry = new Map<string, TourDefinition>();

export function registerTour(tour: TourDefinition) {
  registry.set(tour.id, tour);
}

export function getTour(id: string): TourDefinition | undefined {
  return registry.get(id);
}

export function listTours(): TourDefinition[] {
  return Array.from(registry.values());
}

export function tourSeen(id: string): boolean {
  if (typeof window === "undefined") return true;
  try {
    return localStorage.getItem(`tour-seen-${id}`) === "1";
  } catch {
    return true;
  }
}

export function markTourSeen(id: string) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(`tour-seen-${id}`, "1");
  } catch {
    /* ignore */
  }
}

export function resetTourSeen(id: string) {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(`tour-seen-${id}`);
  } catch {
    /* ignore */
  }
}
