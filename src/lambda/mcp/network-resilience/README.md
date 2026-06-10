# Network Resilience MCP Tool

Multi-region AWS Direct Connect discovery + 22-rule resilience assessment. One Lambda exposes 6 tools (`discover_dx_topology`, `assess_dx_resiliency`, `get_recommendation_details`, `get_dx_pricing`, `estimate_upgrade_cost`, `get_today_date`). Shares the `network_resilience/` package with the `src/lambda/frontend/network-resilience/` REST Lambda — the package is copied into both zips by the Makefile.

## Why this tool is structured differently from the others

Most MCP tools are a single `handler.py` + maybe one helper module. This one holds a full `network_resilience/` Python package with `engine/` (rule evaluation, pricing) and `topology/` (multi-region fetchers, mock fixtures). Reasons:

- **22 rules** split across resiliency and best-practice categories need real code organization, not a monolithic handler.
- **Shared with the frontend REST Lambda.** The `/network-resilience/reassess` endpoint re-runs the same rules on a browser-cached topology — importing from one package keeps results from drifting between the agent path and the fast-path.
- **6 mock scenarios** (`noResiliency`, `devTest`, `high`, `maximum`, `crossAccount`, `cloudWan`) live in `topology/mocks/*.json` so demos work without live AWS.

## Multi-region fan-out

`topology/fetch.py` orchestrates a 5-phase parallel discovery across every reachable region:

1. List DX locations + connections per region (DX API calls).
2. VIFs + DX Gateways + DXGW associations (hub account scope).
3. VPCs + TGWs + TGW attachments (EC2 API, every region in parallel).
4. Cloud WAN core networks + attachments + segments.
5. VPN connections + customer gateways + Maintenance events (Health API).

Partial failures are non-fatal. Every AWS call is wrapped in `logged()` which captures the error to a `fetchErrors` list returned alongside the topology. The visualizer uses this to render inline warnings rather than surfacing a full-page error.

## Cost budget

`discover_dx_topology` can fire 30+ API calls per region for large accounts. Keep a note on the "idempotent within a conversation — call ONCE per session" guidance in `tools.json` — if the supervisor repeatedly calls it, bills add up fast.

## Ghost node spec — what populates and what doesn't

Only two resiliency rules emit ghost-node specs today (`rule_single_dx_location` and `rule_single_connection_per_location`). The other 20 rules return empty `additionalNodes: []` / `additionalEdges: []`. This matches the source SPA exactly (their TypeScript recommendation engine also populates ghosts for only those two rules). If you add a third rule that should emit ghost nodes:

- Inherit from `GhostNodeSpec` dataclass in `types.py`.
- ID convention: `rec-{dxgwId}-{kind}-{suffix}` for per-DXGW ghosts, `rec-{kind}-{suffix}` for global.
- The frontend layout engine picks them up automatically via `useTopologyGraph.collectGhosts()`.

## Cross-account discovery

Currently: TGW attachments reveal VPCs in other accounts via `resourceOwnerId`. No AssumeRole is done — the frontend renders these as "cross-account" badges with whatever ID the TGW attachment reports.

Future: a shared `src/lambda/shared/cross_account.py` AssumeRole helper will let this Lambda (and billing, cost-explorer, cost-opt-hub) enrich cross-account resources when configured role ARNs are present. Explicitly deferred from Phase 7 — needs a broader platform workstream.

## Pricing API is us-east-1 only

`engine/pricing.py` hardcodes `region_name="us-east-1"` when calling the AWS Pricing API. Same pattern as `cost-optimization-hub`. Don't set `AWS_REGION` to anything else in this Lambda's Terraform env vars — it's reserved. Lambda injects it automatically.

## CloudWatch BGP metrics for live status

The REST Lambda (`src/lambda/frontend/network-resilience/handler.py`) fires `GetMetricData` against the `AWS/DX` namespace for `VirtualInterfaceBgpPrefixesAccepted` / `VirtualInterfaceBgpPrefixesAdvertised`. 30-min window, 300s period. Called at 60s intervals by the visualizer Live Status overlay. Same region hint goes in both paths — multi-region topologies call once per region from the frontend, then merge.

## Tests

112 pytest cases in `tests/unit/network_resilience/` cover the rule engine, topology fetchers (mocked AWS responses), recommendation merging, and ghost-node emission. Run: `.venv/bin/pytest tests/unit/network_resilience/ -q`. The full project suite is 253 tests; Makefile's `make test-unit` runs everything.
