"use client";

/**
 * Hook for manually starting/resetting a registered tour.
 *
 * Usage:
 *   const { start, reset, hasSeen } = useTour("visualizer");
 *   <button onClick={start}>Show visualizer tour</button>
 */

import { useCallback } from "react";
import { driver } from "driver.js";
import "driver.js/dist/driver.css";
import { getTour, markTourSeen, resetTourSeen, tourSeen } from "./tour-registry";

export function useTour(tourId: string) {
  const start = useCallback(() => {
    const tour = getTour(tourId);
    if (!tour) {
      console.warn(`[tours] no tour registered for id "${tourId}"`);
      return;
    }
    // Skip steps whose `element` selector doesn't match anything in the
    // current DOM — keeps the flow from dead-ending when an optional UI
    // element (e.g. maintenance calendar) isn't rendered right now.
    const liveSteps = tour.steps.filter((step) => {
      const selector = step.element;
      if (typeof selector !== "string") return true;
      return !!document.querySelector(selector);
    });
    const d = driver({
      showProgress: true,
      allowClose: true,
      stagePadding: 6,
      stageRadius: 10,
      nextBtnText: "Next →",
      prevBtnText: "← Back",
      doneBtnText: "Done",
      progressText: "{{current}} of {{total}}",
      steps: liveSteps,
      onDestroyed: () => markTourSeen(tourId),
    });
    d.drive();
  }, [tourId]);

  const reset = useCallback(() => {
    resetTourSeen(tourId);
  }, [tourId]);

  const hasSeen = useCallback(() => tourSeen(tourId), [tourId]);

  return { start, reset, hasSeen };
}
