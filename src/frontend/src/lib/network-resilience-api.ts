/**
 * TypeScript client for the network-resilience-api Lambda (Phase 4).
 *
 * Routes live under the same API Gateway as the core frontend-api
 * (``FRONTEND_API_URL`` / ``/config.json`` ``frontend_api_url``). Only
 * deployed when ``network-resiliency-agent`` is in ``DEPLOY_AGENTS`` — so
 * callers should feature-detect via ``/health`` before showing UI that
 * depends on these routes.
 */

import type { TopologyData, CombinedAssessment } from "./topology";

type GetTokenFn = () => Promise<string | null>;

async function resolveBaseUrl(): Promise<string> {
  // Re-use the core API url — same gateway, different Lambda behind it.
  const envUrl = process.env.NEXT_PUBLIC_FRONTEND_API_URL || "";
  if (envUrl) return envUrl.replace(/\/$/, "");
  try {
    const resp = await fetch("/config.json");
    if (resp.ok) {
      const cfg = (await resp.json()) as Record<string, string>;
      if (cfg.frontend_api_url) return cfg.frontend_api_url.replace(/\/$/, "");
    }
  } catch {
    /* fall through */
  }
  throw new Error(
    "frontend API URL not configured (NEXT_PUBLIC_FRONTEND_API_URL or /config.json)",
  );
}

async function post<T>(
  path: string,
  body: unknown,
  getToken: GetTokenFn,
): Promise<T> {
  const base = await resolveBaseUrl();
  const token = await getToken();
  const res = await fetch(`${base}${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const payload = await res.json();
      if (payload?.error) detail = payload.error;
    } catch {
      /* non-JSON body */
    }
    throw new Error(`network-resilience-api ${path} failed: ${detail}`);
  }
  return (await res.json()) as T;
}

// ----- Health / feature-detect ---------------------------------------------

export interface HealthResponse {
  status: string;
  version: string;
}

export async function getNrApiHealth(
  getToken: GetTokenFn,
): Promise<HealthResponse | null> {
  try {
    const base = await resolveBaseUrl();
    const token = await getToken();
    const res = await fetch(`${base}/network-resilience/health`, {
      headers: token ? { authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) return null;
    return (await res.json()) as HealthResponse;
  } catch {
    return null;
  }
}

// ----- /reassess -----------------------------------------------------------

export interface ReassessRequest {
  topology: TopologyData;
  targetTiers?: "high" | "maximum" | Record<string, "high" | "maximum">;
}

export interface ReassessResponse {
  assessment: CombinedAssessment;
}

/**
 * Re-run the 22 resiliency rules on a cached topology with updated target
 * tiers. Pure compute; <500ms target. Called on every tier-picker toggle in
 * the visualizer toolbar without a chat turn.
 */
export async function reassess(
  req: ReassessRequest,
  getToken: GetTokenFn,
): Promise<ReassessResponse> {
  return post<ReassessResponse>(
    "/network-resilience/reassess",
    req,
    getToken,
  );
}

// ----- /live-status --------------------------------------------------------

export interface LiveStatusRequest {
  vifIds: string[];
  region?: string;
}

export interface LiveStatusResponse {
  region: string;
  metrics: Record<string, { accepted?: number; advertised?: number }>;
}

export async function fetchLiveStatus(
  req: LiveStatusRequest,
  getToken: GetTokenFn,
): Promise<LiveStatusResponse> {
  return post<LiveStatusResponse>(
    "/network-resilience/live-status",
    req,
    getToken,
  );
}

// ----- /utilization --------------------------------------------------------

export type UtilizationWindowDays = 30 | 60 | 90;

export interface UtilizationRequest {
  vifIds?: string[];
  connectionIds?: string[];
  region?: string;
  windowDays: UtilizationWindowDays;
}

export interface UtilizationResponse {
  region: string;
  windowDays: UtilizationWindowDays;
  vif: Record<string, { ingressBpsPeak?: number; egressBpsPeak?: number }>;
  connection: Record<string, { ingressBpsPeak?: number; egressBpsPeak?: number }>;
}

/**
 * Fetch peak hourly bps utilization per VIF + per DX Connection over the
 * selected 30 / 60 / 90 day window. Server delegates to the same shared
 * `network_resilience.topology.cloudwatch_dx.fetch_utilization` the agent
 * topology fetcher uses, so the math matches `discover_dx_topology`.
 */
export async function fetchUtilization(
  req: UtilizationRequest,
  getToken: GetTokenFn,
): Promise<UtilizationResponse> {
  return post<UtilizationResponse>(
    "/network-resilience/utilization",
    req,
    getToken,
  );
}

// ----- /cross-account-enrich (Phase 7) -------------------------------------

export interface CrossAccountEnrichRequest {
  topology: TopologyData;
  roleArns: string[];
}

export interface CrossAccountEnrichResponse {
  additionalVpcs: unknown[];
  additionalTgws: unknown[];
  additionalTgwAttachments: unknown[];
  note?: string;
}

export async function crossAccountEnrich(
  req: CrossAccountEnrichRequest,
  getToken: GetTokenFn,
): Promise<CrossAccountEnrichResponse> {
  return post<CrossAccountEnrichResponse>(
    "/network-resilience/cross-account-enrich",
    req,
    getToken,
  );
}
