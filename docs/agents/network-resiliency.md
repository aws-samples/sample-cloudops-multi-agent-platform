# Network Resiliency agent — architecture, deploy modes, operations

End-to-end reference for the `network-resiliency-agent`. Lives under
`ops-excellence-agent` (peer of `health-events-agent`). Discovers AWS
Direct Connect topology across all reachable regions and evaluates it
against 22 resiliency + best-practice rules.

---

## 1. What the feature does

Answers questions about AWS Direct Connect topology, resilience
posture, and DX-specific pricing. Four complementary tools:

- **`discover_dx_topology`** — multi-region parallel discovery of DX
  connections, VIFs, DX Gateways, Transit Gateways, VPCs, VPNs,
  Cloud WAN. Returns a normalized `TopologyData` JSON.
- **`assess_dx_resiliency`** — runs 5 resiliency rules + 17
  best-practice checks + 2 SLA-precondition attestations against the
  topology. Returns per-DXGW scores (0–100), current/target tiers,
  categorized recommendations.
- **`get_recommendation_details`** — expands one recommendation by
  ID.
- **`get_dx_pricing`** / **`estimate_upgrade_cost`** — live DX port
  and data-transfer pricing, plus delta-to-reach-target-tier
  estimates.

Representative prompts:

- `"What does my Direct Connect topology look like?"`
- `"Is my DX resilient enough to qualify for the 99.99% SLA?"`
- `"What do I need to add to reach Maximum tier?"`
- `"How much would upgrading cost?"`
- `"Show me a demo topology — 'maximum' scenario."`

Behavior:

```
"Is my DX resilient enough for 99.99%?"
  → supervisor → ops-excellence-agent → network-resiliency-agent
    → discover_dx_topology (once per session)
    → assess_dx_resiliency(targets="maximum")
    → per-DXGW score + list of gaps to close
```

The agent prompt hard-gates `discover_dx_topology` to **ONCE per
conversation** — the call can fire 30+ AWS API calls per region and
bills add up fast if repeated.

---

## 2. Structural difference from other leaves

Unlike every other leaf agent which has a single `handler.py`, this
one ships a full Python package under
`src/lambda/mcp/network-resilience/network_resilience/` with
`engine/` (rule evaluation, pricing) and `topology/` (multi-region
fetchers, mock fixtures). The package is **also** copied into the
`src/lambda/frontend/network-resilience/` Lambda so the `/reassess`
REST endpoint runs the same rules on a browser-cached topology.

Reasons:
- 22 rules split across resiliency + best-practice categories need
  real code organization, not a monolithic handler.
- Shared between the agent path (MCP) and a fast-path REST call
  (frontend visualizer's live reassess). One rule engine, two
  entry points — prevents result drift.
- 6 mock scenarios (`noResiliency`, `devTest`, `high`, `maximum`,
  `crossAccount`, `cloudWan`) live in `topology/mocks/*.json` so
  demos work without live AWS.

See `src/lambda/mcp/network-resilience/README.md` for the
in-package reference — it has the canonical detail.

---

## 3. Deploy modes

```
How is your DX set up?
├── Single account, your DX lives here      → Mode A (single-account)
└── Multi-account with cross-account TGW    → Mode B (dynamic fan-out)
```

### Mode A — single-account

All DX resources (connections, VIFs, DXGWs, TGWs) live in the
account running cloudops.

- **What works:** everything. `discover_dx_topology` fans out across
  all reachable regions using the Lambda's execution role.
- **Terraform:** no extra config. Leave cross-account role vars
  unset.

### Mode B — dynamic cross-account fan-out

TGW attachments reveal VPCs in other accounts via
`resourceOwnerId`. The agent surfaces those resources in the topology
with a "cross-account" indicator.

**Today:** the resources render as "cross-account" badges with
whatever ID the TGW attachment reports. No AssumeRole happens — we
just display what TGW already tells us.

**Future** (see `temp/optimizations.md`): a broader cross-account
enrichment pass that assumes into spoke accounts to fetch full VPC
detail. Deliberately deferred from the initial release.

For today, `CROSS_ACCOUNT_ROLE_ARN` and
`CROSS_ACCOUNT_ROLE_ARNS` are not used by this tool.

---

## 4. Tiers and scoring

Each DX Gateway is scored 0–100 against a target tier (`high` or
`maximum`). Tier definitions match AWS's published SLA preconditions:

| Tier | SLA | Minimum topology |
|---|---|---|
| `none` | — | Any DX connection |
| `devtest` | no SLA | Single connection |
| `high` | 99.9% | ≥2 DX locations |
| `maximum` | 99.99% | ≥2 DX locations AND ≥2 AWS logical devices per location |

Scoring considers location count, connection count, device
diversity, and VIF health. 5 resiliency rules + 17 best-practice
checks generate `critical` / `warning` / `info` recommendations.
Only two rules emit ghost-node specs today
(`rule_single_dx_location`, `rule_single_connection_per_location`);
the rest return empty `additionalNodes: []` — intentional, matches
the source SPA.

---

## 5. Cost budget for `discover_dx_topology`

Rough API call volume per `discover_dx_topology` invocation, per
reachable region:

- DirectConnect: 5–8 calls (connections, VIFs, DXGWs, associations, LAGs, locations).
- EC2: 8–10 calls (VPCs, TGWs, attachments, route tables, VPN gateways, customer gateways).
- NetworkManager: 4 calls (core networks, attachments, peerings, routes).
- Health: 3 calls (events, details, affected entities).
- CloudWatch: 2 calls (metrics).

Conservatively 25–30 calls × N regions. For a topology in 5 regions
that's 125–150 API calls per discovery. Keep the "call ONCE per
conversation" guidance in the prompt honored.

---

## 6. Pricing API — us-east-1 only

`get_dx_pricing` and `estimate_upgrade_cost` use the AWS Pricing API
which is **us-east-1-only** regardless of where your DX lives. The
engine's `pricing.py` hardcodes `region_name="us-east-1"`. Same
pattern as `pricing-agent` and `cost-optimization-hub`.

---

## 7. Data model — TopologyData (high-level)

```
{
  "connections": [...],     // DX connections across all regions
  "virtualInterfaces": [...],
  "dxGateways": [...],
  "dxGatewayAssociations": [...],
  "transitGateways": [...],
  "tgwAttachments": [...],
  "vpcs": [...],
  "vpnGateways": [...],
  "vpnConnections": [...],
  "coreNetworks": [...],    // Cloud WAN
  "healthEvents": [...],
  "fetchErrors": [           // Partial failures, non-fatal
    {"region": "us-east-2", "api": "DescribeConnections", "error": "..."}
  ]
}
```

Partial failures are deliberately captured rather than aborting the
whole discovery. The frontend visualizer renders inline warnings.

### `CombinedAssessment` (from `assess_dx_resiliency`)

```
{
  "globalScore": 62,
  "globalTier": "devtest",
  "targetTier": "high",
  "perDxGateway": [
    {"id": "dxgw-abc", "name": "...", "currentTier": "high",
     "targetTier": "high", "score": 92, "scoringRationale": {...}}
  ],
  "resiliency": {"recommendations": [...]},
  "bestPractice": {"recommendations": [...]},
  "attestations": [...]      // SLA preconditions
}
```

---

## 8. Report template — `dx_resiliency_report`

Ships with six sections mirroring the assessment output. See
[`src/agents/shared/report_templates/dx_resiliency_report.json`](../../src/agents/shared/report_templates/dx_resiliency_report.json).

---

## 9. Live BGP status (frontend-only)

The frontend visualizer polls CloudWatch `AWS/DX` metrics
(`VirtualInterfaceBgpPrefixesAccepted`,
`VirtualInterfaceBgpPrefixesAdvertised`) every 60 seconds to render
a live BGP overlay. This is done by the
`src/lambda/frontend/network-resilience/` REST Lambda (NOT this
agent). 30-minute window, 300-second period.

---

## 10. Known gotchas

| Symptom | Likely cause | Fix |
|---|---|---|
| `discover_dx_topology` returns empty for some regions | Region doesn't have DX service endpoint, or IAM denies | Check `fetchErrors` in the response — per-region, per-API error breakdown is there |
| Agent re-fetches topology on every question | Worker prompt rule not being honored, or user explicitly asked to refresh | Prompt already says "call ONCE per conversation"; if ignored, tighten the phrasing |
| `assess_dx_resiliency` scores look off | Missing topology data from a region with DX resources | Check `fetchErrors` first — partial topology produces partial assessment |
| Pricing tool returns $0 | Wrong `port_speed` format (must be `1Gbps`, `10Gbps`, `100Gbps` — case matters) | Use exact values from the schema |
| Cross-account VPC shows as "unknown owner" | TGW attachment's `ResourceOwnerId` is missing or unresolvable | Expected today; future cross-account enrichment will resolve it |
| Mock scenario selector doesn't work | Wrong scenario name | Valid: `noResiliency`, `devTest`, `high`, `maximum`, `crossAccount`, `cloudWan` |
| `estimate_upgrade_cost` disagrees with AWS pricing page | The estimate excludes data transfer, cross-connect fees, and partner pricing | Use as a budgeting estimate only; confirm specifics with `get_dx_pricing` |

---

## 11. Deferred upstream port — resizable chat input

The upstream standalone visualizer ships a drag-handle that lets the user
resize the chat-input height (36–400px), persists the height to
localStorage, resets on topology refresh, and offers keyboard a11y via
arrow keys + double-click to reset.

This is **intentionally not ported** into this project right now:

- Upstream has a dedicated `ChatInput.tsx` component that owns its
  height. In this project the AG-UI Next.js frontend owns the chat
  surface, not the visualizer panel — so the upstream change would have
  to be translated against the AG-UI chat component, not copy-pasted
  into `src/frontend/src/components/visualizer/`.
- Upstream persists height to a localStorage key and bumps the key to
  `v2` on topology refresh. The AG-UI frontend uses DynamoDB-backed
  sessions via the core-api Lambda — a per-browser localStorage hint is
  fine for a client-side setting, but collides with the "session owns
  conversation state" contract.
- The UX desirability in this project's UI is not yet scoped — the AG-UI
  chat input is already markdown-aware and much larger than the
  standalone visualizer's.

To port when the time comes: locate the master chat input component
inside `src/frontend/src/` (AG-UI chat surface, NOT the visualizer
panel), decide whether a localStorage-backed height is acceptable
alongside the session model, and translate the drag / keyboard / reset
behaviors from the upstream component.
