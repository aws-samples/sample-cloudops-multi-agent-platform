"use client";

/**
 * On-demand fetcher for the "Show utilization" overlay.
 *
 * Triggers `POST /network-resilience/utilization` whenever the user has
 * `showUtilization` on AND the cache for the active region+window is empty.
 * Results merge into `topologyData.vifUtilization` /
 * `topologyData.connectionUtilization` so `topology-builder.ts` hangs the
 * `utilizationIngressBps` / `utilizationEgressBps` data fields onto edges.
 *
 * Caching: keyed by `${region}:${windowDays}`. Flipping the window selector
 * 30↔60↔90 reuses prior fetches within the session instead of re-billing
 * CloudWatch. The cache is cleared on topology refresh (the Map gets reset
 * implicitly when the user reloads).
 *
 * Multi-region topologies fan out one POST per region in parallel.
 */

import { useEffect, useRef } from "react";
import { useTopologyStore } from "@/lib/topology/store";
import { fetchUtilization } from "@/lib/network-resilience-api";
import { getToken } from "@/lib/auth";

export function useUtilization() {
  const showUtilization = useTopologyStore((s) => s.showUtilization);
  const utilizationWindow = useTopologyStore((s) => s.utilizationWindow);
  const topologyData = useTopologyStore((s) => s.topologyData);
  const setTopologyData = useTopologyStore((s) => s.setTopologyData);
  const utilizationCache = useTopologyStore((s) => s.utilizationCache);
  const setUtilizationCacheEntry = useTopologyStore((s) => s.setUtilizationCacheEntry);
  const inflight = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!showUtilization || !topologyData) return;

    // Group VIFs and Connections by region so each region gets exactly one
    // CloudWatch round-trip.
    const vifsByRegion = new Map<string, string[]>();
    const connsByRegion = new Map<string, string[]>();
    for (const vif of topologyData.virtualInterfaces) {
      const region = vif.region || "";
      if (!region) continue;
      const list = vifsByRegion.get(region) ?? [];
      list.push(vif.virtualInterfaceId);
      vifsByRegion.set(region, list);
    }
    for (const conn of topologyData.connections) {
      const region = conn.region || "";
      if (!region) continue;
      const list = connsByRegion.get(region) ?? [];
      list.push(conn.connectionId);
      connsByRegion.set(region, list);
    }
    const regions = new Set<string>([
      ...vifsByRegion.keys(),
      ...connsByRegion.keys(),
    ]);
    if (regions.size === 0) return;

    // Determine which regions still need a fetch (cache miss) for this window.
    const regionsToFetch: string[] = [];
    for (const region of regions) {
      const key = `${region}:${utilizationWindow}`;
      if (!utilizationCache.has(key)) regionsToFetch.push(region);
    }

    const apply = () => {
      const mergedVif = new Map<string, { ingressBpsPeak?: number; egressBpsPeak?: number }>();
      const mergedConn = new Map<string, { ingressBpsPeak?: number; egressBpsPeak?: number }>();
      for (const region of regions) {
        const key = `${region}:${utilizationWindow}`;
        const entry = utilizationCache.get(key);
        if (!entry) continue;
        for (const [k, v] of entry.vif) mergedVif.set(k, v);
        for (const [k, v] of entry.connection) mergedConn.set(k, v);
      }
      // No-op if nothing in cache yet (fetch still in flight) — the next
      // effect run after the fetch resolves will pick it up.
      if (mergedVif.size === 0 && mergedConn.size === 0) return;
      const sameWindow = topologyData.utilizationWindowDays === utilizationWindow;
      if (
        sameWindow &&
        topologyData.vifUtilization === mergedVif &&
        topologyData.connectionUtilization === mergedConn
      ) return;
      setTopologyData({
        ...topologyData,
        vifUtilization: mergedVif,
        connectionUtilization: mergedConn,
        utilizationWindowDays: utilizationWindow,
      });
    };

    if (regionsToFetch.length === 0) {
      apply();
      return;
    }

    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;

    (async () => {
      try {
        const results = await Promise.all(
          regionsToFetch.map((region) =>
            fetchUtilization(
              {
                vifIds: vifsByRegion.get(region) ?? [],
                connectionIds: connsByRegion.get(region) ?? [],
                region,
                windowDays: utilizationWindow,
              },
              async () => getToken(),
            ),
          ),
        );
        if (controller.signal.aborted) return;
        for (let i = 0; i < regionsToFetch.length; i++) {
          const region = regionsToFetch[i];
          const r = results[i];
          const vifMap = new Map<string, { ingressBpsPeak?: number; egressBpsPeak?: number }>(
            Object.entries(r.vif),
          );
          const connMap = new Map<string, { ingressBpsPeak?: number; egressBpsPeak?: number }>(
            Object.entries(r.connection),
          );
          setUtilizationCacheEntry(`${region}:${utilizationWindow}`, {
            vif: vifMap,
            connection: connMap,
          });
        }
        apply();
      } catch (err) {
        if (controller.signal.aborted) return;
        console.error("[utilization] fetch failed:", err);
      }
    })();

    return () => {
      controller.abort();
    };
    // `topologyData` itself is mutated by `apply()` — depending on it would
    // loop. Subscribe to the inputs that should re-trigger a fetch + merge.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    showUtilization,
    utilizationWindow,
    topologyData?.virtualInterfaces,
    topologyData?.connections,
    utilizationCache,
    setTopologyData,
    setUtilizationCacheEntry,
  ]);
}
