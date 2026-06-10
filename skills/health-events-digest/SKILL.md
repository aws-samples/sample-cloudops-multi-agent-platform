---
name: health-events-digest
description: "AWS Health Events digest — critical/high events, impact summaries, remediation hints, and operational timeline. Three paths: (1) MCP tools via deployed gateway (preferred); (2) direct AWS CLI; (3) delegation to coding agent. Produces structured markdown digest. Use when the user asks about AWS health events, service issues, maintenance, outages, or operational incidents."
argument-hint: "[what do you want to know? e.g. 'critical events', 'health events this week', 'any upcoming maintenance']"
allowed-tools: Bash, Write, Read, Glob, Grep
user-invocable: true
---

# AWS Health Events Digest

Surface AWS health events — service disruptions, scheduled maintenance, account notifications — with risk scoring and remediation guidance. Output is a structured markdown digest.

## Routing

```
Are health-events MCP tools available (get_critical_events, get_health_events, etc.)?
├── Yes → Path M (use MCP tools — pre-enriched with risk scoring + LLM narratives)
└── No
    ├── Can you run `aws --version`?
    │   ├── Yes → Path A (AWS CLI — aws health describe-events)
    │   └── No
    │       ├── Is a coding agent enabled? → Path B (delegate)
    │       └── No → Stop. Inform user to deploy gateway-only or use Claude Code/Kiro.
```

## Path M — MCP Tools (preferred)

The health-events MCP tools query a pre-populated DynamoDB table with enriched events (rules-based risk scoring + LLM-generated impact summaries). This is richer than raw AWS Health API output.

| Tool | Use for |
|------|---------|
| `get_critical_events` | CRITICAL and HIGH risk events, most recent first |
| `get_health_events` | Flexible query with filters (status, service, region, date range) |
| `get_events_by_account` | All events for a specific account |
| `get_events_by_service` | Events for a specific AWS service |
| `get_recent_events` | Last N days of events |
| `get_event_summary` | Counts grouped by status, service, region, or risk level |
| `get_event_by_arn` | Single event detail by ARN |

### Workflow

1. **Classify the request** — critical events? specific service? time range? summary?
2. **Call 1-2 tools** — usually `get_critical_events` or `get_health_events` with filters
3. **Format as digest** using the output template below

### Key fields in tool response

Each event includes:
- `riskLevel` — CRITICAL / HIGH / MEDIUM / LOW (rules-based)
- `impactSummary` — one-sentence operational summary (LLM-generated)
- `remediationHint` — what an operator should check/do (LLM-generated)
- `eventTypeCategory` — scheduledChange / accountNotification / issue
- `statusCode` — open / closed / upcoming
- `service`, `region`, `startTime`, `lastUpdateTime`

## Path A — AWS CLI

If MCP tools aren't available:

```bash
# Requires Business+ AWS Support plan for DescribeEvents
aws health describe-events \
  --filter "eventStatusCodes=open,upcoming" \
  --region us-east-1 \  # Health API is us-east-1 only
  --query 'events[*].{arn:arn,service:service,region:region,status:statusCode,category:eventTypeCategory,start:startTime,lastUpdate:lastUpdatedTime}'

# For org-wide (requires Organizations + Health org view enabled)
aws health describe-events-for-organization \
  --filter "eventStatusCodes=open,upcoming"
```

Note: raw CLI output won't have risk scoring or LLM narratives — you'll need to assess severity manually based on `eventTypeCategory` and service impact.

## Path B — Delegate

Hand the coding agent:
> "Query AWS Health events (open + upcoming) via aws health describe-events in us-east-1. Format as a markdown table with service, region, status, category, and start time. Flag any issues (eventTypeCategory=issue) as high priority."

## Output Template

Always produce this structure:

```markdown
# AWS Health Events Digest

**Period:** [date range or "current open events"]
**Account:** [account ID]
**Generated:** [timestamp]

## Critical & High Priority

| # | Service | Region | Risk | Status | Summary |
|---|---------|--------|------|--------|---------|
| 1 | [service] | [region] | 🔴 CRITICAL | [open/upcoming] | [impactSummary] |
| 2 | [service] | [region] | 🟠 HIGH | [open/upcoming] | [impactSummary] |

### Details

**[Event 1 title/service]**
- **ARN:** `[arn]`
- **Started:** [date]
- **Impact:** [impactSummary]
- **Action:** [remediationHint]

---

## Scheduled Maintenance

| # | Service | Region | Window | Impact |
|---|---------|--------|--------|--------|
| 1 | [service] | [region] | [start – end] | [summary] |

## Summary

| Category | Count |
|----------|-------|
| Critical/High | [n] |
| Medium/Low | [n] |
| Scheduled Maintenance | [n] |
| Closed (last 7 days) | [n] |

## Recommendations

- [prioritized action items based on open critical events]
```

## Constraints

- Never fabricate health events. All data must come from tool/CLI output
- Risk levels: only use CRITICAL/HIGH/MEDIUM/LOW as returned by the tool
- If no events found, say "No active health events" — don't invent issues
- AWS Health API requires Business+ Support plan — if CLI returns SubscriptionRequiredException, inform the user
- The health-events MCP tools read from a pre-populated DynamoDB table; if it's empty, suggest `make backfill-health DAYS=90`
