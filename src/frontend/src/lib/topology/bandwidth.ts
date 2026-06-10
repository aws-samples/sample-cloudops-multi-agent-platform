/**
 * Helpers for converting between AWS DX `bandwidth` strings ("1Gbps",
 * "100Mbps", "10Gbps", etc.) and a normalized bps integer used by the
 * utilization edge overlay to compute % of port capacity.
 */

export function parseBandwidthToBps(bw?: string): number | undefined {
  if (!bw) return undefined;
  const m = bw.match(/^\s*(\d+(?:\.\d+)?)\s*(G|M|K)?bps\s*$/i);
  if (!m) return undefined;
  const value = parseFloat(m[1]);
  const unit = (m[2] ?? "").toUpperCase();
  const mult = unit === "G" ? 1e9 : unit === "M" ? 1e6 : unit === "K" ? 1e3 : 1;
  return value * mult;
}

export function formatBps(bps: number): string {
  if (bps >= 1e9) return `${(bps / 1e9).toFixed(2)} Gbps`;
  if (bps >= 1e6) return `${(bps / 1e6).toFixed(1)} Mbps`;
  if (bps >= 1e3) return `${(bps / 1e3).toFixed(0)} Kbps`;
  return `${bps.toFixed(0)} bps`;
}
