"use client";

/**
 * Renders nothing, but auto-starts a registered tour the first time it sees
 * a live `data-tour` selector in the DOM. Waits for the target element to
 * exist before firing — prevents the tour from running against a pre-hydration
 * layout that the user isn't looking at yet.
 *
 * Mount this once per feature next to the tour's first step target. For the
 * visualizer, it lives inside `VisualizerPanel`.
 */

import { useEffect, useRef } from "react";
import { driver } from "driver.js";
import "driver.js/dist/driver.css";
import { getTour, markTourSeen, tourSeen } from "./tour-registry";

interface TourAutoStarterProps {
  tourId: string;
  /** CSS selector whose presence gates the auto-start. */
  waitFor?: string;
  /** Delay (ms) after the selector resolves before firing. Defaults to 600. */
  delayMs?: number;
}

export function TourAutoStarter({ tourId, waitFor, delayMs = 600 }: TourAutoStarterProps) {
  const fired = useRef(false);

  useEffect(() => {
    if (fired.current) return;
    if (tourSeen(tourId)) return;

    const tour = getTour(tourId);
    if (!tour || tour.autoStart === false) return;

    const checkAndFire = () => {
      if (fired.current) return;
      if (waitFor && !document.querySelector(waitFor)) return false;

      fired.current = true;
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
      return true;
    };

    // Poll up to ~3s for the target selector; after that we give up.
    // First attempt waits `delayMs` so React has a paint cycle.
    let attempts = 0;
    const id = window.setInterval(() => {
      attempts += 1;
      if (checkAndFire() || attempts > 30) window.clearInterval(id);
    }, 100);
    const kickoff = window.setTimeout(() => {
      // First attempt after the initial delay.
      // Interval already handles follow-ups.
    }, delayMs);
    return () => {
      window.clearInterval(id);
      window.clearTimeout(kickoff);
    };
  }, [tourId, waitFor, delayMs]);

  return null;
}
