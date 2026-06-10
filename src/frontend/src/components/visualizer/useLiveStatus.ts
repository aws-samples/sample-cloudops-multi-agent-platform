"use client";

/**
 * 60-second BGP polling loop tied to the `showLiveStatus` store flag.
 *
 * When enabled, calls `POST /network-resilience/live-status` with every VIF
 * ID in the current topology. The response arrives as a plain object keyed by
 * VIF id (`{accepted, advertised}`) — merged into `topologyData.bgpPrefixMetrics`
 * (a Map) so edge components pick up the refreshed counter without prop-drilling.
 *
 * Multi-region topologies are split by VIF.region and polled per region; the
 * results are merged into a single map before commit.
 */

import { useEffect, useRef } from "react";
import { useTopologyStore } from "@/lib/topology/store";
import { fetchLiveStatus } from "@/lib/network-resilience-api";
import { getToken } from "@/lib/auth";

const POLL_INTERVAL_MS = 60_000;

export function useLiveStatus() {
  const showLiveStatus = useTopologyStore((s) => s.showLiveStatus);
  const topologyData = useTopologyStore((s) => s.topologyData);
  const setTopologyData = useTopologyStore((s) => s.setTopologyData);
  const inflight = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!showLiveStatus || !topologyData) return;

    const vifsByRegion = new Map<string, string[]>();
    for (const vif of topologyData.virtualInterfaces) {
      const region = vif.region || "";
      if (!region) continue;
      const existing = vifsByRegion.get(region);
      if (existing) existing.push(vif.virtualInterfaceId);
      else vifsByRegion.set(region, [vif.virtualInterfaceId]);
    }
    if (vifsByRegion.size === 0) return;

    const poll = async () => {
      inflight.current?.abort();
      const controller = new AbortController();
      inflight.current = controller;
      try {
        const results = await Promise.all(
          Array.from(vifsByRegion.entries()).map(([region, vifIds]) =>
            fetchLiveStatus({ vifIds, region }, async () => getToken()),
          ),
        );
        if (controller.signal.aborted) return;
        const merged = new Map<string, { accepted?: number; advertised?: number }>(
          topologyData.bgpPrefixMetrics ? topologyData.bgpPrefixMetrics : [],
        );
        for (const r of results) {
          for (const [vifId, counters] of Object.entries(r.metrics)) {
            merged.set(vifId, counters);
          }
        }
        // Replace the store's topology with an updated bgpPrefixMetrics map.
        // Node components re-read through `s.topologyData?.bgpPrefixMetrics`.
        setTopologyData({ ...topologyData, bgpPrefixMetrics: merged });
      } catch (err) {
        if (controller.signal.aborted) return;
        console.error("[live-status] poll failed:", err);
      }
    };

    poll();
    const id = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      clearInterval(id);
      inflight.current?.abort();
    };
    // `topologyData` in deps would cause an infinite loop since we mutate it
    // on every successful poll. Track it by reference — re-subscribe only when
    // the user toggles the overlay or the topology changes shape.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showLiveStatus, topologyData?.virtualInterfaces, setTopologyData]);
}
