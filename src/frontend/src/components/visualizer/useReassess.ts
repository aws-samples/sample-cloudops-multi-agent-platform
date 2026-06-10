"use client";

/**
 * Fast-path reassessment driver.
 *
 * When the user flips a tier in `resiliencyTargets` (via `TargetTierPicker`
 * or `BulkTargetTierPicker`), recompute the assessment locally with the
 * ported TypeScript engine and replace it in the store. Mirrors the upstream
 * SPA: target-tier flips are instant, no network round-trip, no chat turn.
 */

import { useEffect, useRef } from "react";
import { useTopologyStore } from "@/lib/topology/store";
import { analyzeTopology } from "@/lib/topology";

const DEBOUNCE_MS = 120;

export function useReassess() {
  const topologyData = useTopologyStore((s) => s.topologyData);
  const resiliencyTargets = useTopologyStore((s) => s.resiliencyTargets);
  const setAssessment = useTopologyStore((s) => s.setAssessment);
  const isInitialRender = useRef(true);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (isInitialRender.current) {
      isInitialRender.current = false;
      return;
    }
    if (!topologyData) return;
    if (Object.keys(resiliencyTargets).length === 0) return;

    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => {
      try {
        setAssessment(analyzeTopology(topologyData, resiliencyTargets));
      } catch (err) {
        console.error("[reassess] failed:", err);
      }
    }, DEBOUNCE_MS);

    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
    };
  }, [topologyData, resiliencyTargets, setAssessment]);
}
