---
name: finops-analysis
description: "AWS FinOps analysis — cost breakdown, trends, forecasts, anomalies, and optimization recommendations. Three paths: (1) MCP tools via deployed gateway (preferred); (2) direct AWS CLI; (3) delegation to coding agent. Produces markdown tables and summaries. Use when the user asks about AWS costs, spend, budget, savings, optimization, or cost trends."
argument-hint: "[what do you want to know? e.g. 'top services by cost', 'cost forecast', 'savings recommendations']"
allowed-tools: Bash, Write, Read, Glob, Grep
user-invocable: true
---

# AWS FinOps Analysis

Analyze AWS costs, produce spend breakdowns, identify trends, surface anomalies, and recommend optimizations. Output is markdown — tables, summaries, and actionable recommendations.

## Routing

```
Are cost-explorer / billing / cost-optimization-hub MCP tools available?
├── Yes → Path M (use MCP tools — fastest, handles cross-account)
└── No
    ├── Can you run `aws --version`?
    │   ├── Yes → Path A (AWS CLI directly)
    │   └── No
    │       ├── Is a coding agent enabled? → Path B (delegate)
    │       └── No → Stop. Tell the user to deploy with DEPLOY_MODE=gateway-only
    │                    or run in Claude Code / Kiro.
```

## Path M — MCP Tools (preferred)

When these tools are available, use them directly:

| Tool | Use for |
|------|---------|
| `get_cost_and_usage` | Spend by service, account, region, tag. Monthly or daily granularity |
| `get_cost_and_usage_comparisons` | Month-over-month or period-over-period changes |
| `get_cost_forecast` | Projected spend for current/next month |
| `get_dimension_values` | List available services, accounts, regions for filtering |
| `get_anomalies` | Cost anomalies detected by AWS Cost Anomaly Detection |
| `get_billing_alerts` | Active budgets and their current vs limit status |
| `get_account_info` | Account ID, org membership, billing details |
| `get_enrollment_status` | Check if Cost Optimization Hub is enabled |
| `list_recommendations` | Right-sizing, idle resources, savings plans, reserved instances |
| `list_recommendation_summaries` | Aggregated savings by resource type or region |

### Workflow

1. **Understand the question** — classify: spend breakdown, trend, forecast, anomaly check, or optimization
2. **Call the appropriate tools** — don't over-fetch. One or two tool calls usually suffice
3. **Format as markdown** — tables for data, bullet points for insights, bold for key numbers

### Example flows

**"What are my top services by cost this month?"**
→ `get_cost_and_usage` with Granularity=MONTHLY, GroupBy=SERVICE, current month

**"How does this month compare to last?"**
→ `get_cost_and_usage_comparisons` comparing current to prior month

**"Any savings opportunities?"**
→ `get_enrollment_status` first (check COH is enabled), then `list_recommendations`

**"Cost forecast?"**
→ `get_cost_forecast` for current month

## Path A — AWS CLI

If MCP tools aren't available but AWS CLI is:

```bash
# Top services by cost (current month)
aws ce get-cost-and-usage \
  --time-period Start=$(date -u +%Y-%m-01),End=$(date -u +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics AmortizedCost \
  --group-by Type=DIMENSION,Key=SERVICE

# Forecast
aws ce get-cost-forecast \
  --time-period Start=$(date -u +%Y-%m-%d),End=$(date -u +%Y-%m-01 -d "+1 month") \
  --granularity MONTHLY \
  --metric AMORTIZED_COST

# Anomalies (last 30 days)
aws ce get-anomalies \
  --date-interval Start=$(date -u -d "-30 days" +%Y-%m-%d),End=$(date -u +%Y-%m-%d)

# Optimization recommendations
aws cost-optimization-hub list-recommendations --max-results 20
```

Parse JSON outputs and format as markdown tables.

## Path B — Delegate

Hand the coding agent this brief:
> "Run AWS Cost Explorer CLI commands to answer: [user's question]. Return the results as markdown tables with cost figures in USD. Include month-over-month comparison if relevant."

## Output format

Always produce:
- **Summary line** — one sentence with the key number (total spend, top service, savings available)
- **Data table** — ranked, with columns for service/account, cost, change
- **Insights** — 2-3 bullet points on what stands out (spikes, anomalies, trends)
- **Recommendations** — if optimization data is available, prioritized list

## Output Template

```markdown
# FinOps Analysis

**Period:** [month/date range]
**Account:** [account ID or "Organization"]
**Generated:** [timestamp]

## Spend Summary

| Metric | Value |
|--------|-------|
| Total spend (period) | $[X,XXX.XX] |
| Month-over-month change | [+/-X.X]% |
| Forecast (end of month) | $[X,XXX.XX] |
| Top service | [service] ($[X,XXX]) |

## Top Services by Cost

| # | Service | Cost (USD) | MoM Change | % of Total |
|---|---------|-----------|-----------|-----------|
| 1 | [service] | $[X,XXX] | [+/-X]% | [X]% |
| 2 | [service] | $[X,XXX] | [+/-X]% | [X]% |

## Anomalies

| Service | Detected | Expected | Actual | Impact |
|---------|----------|----------|--------|--------|
| [service] | [date] | $[X] | $[X] | +$[X] |

_(or "No anomalies detected in this period")_

## Optimization Opportunities

| # | Resource | Type | Action | Monthly Savings |
|---|----------|------|--------|----------------|
| 1 | [id] | [type] | [right-size/delete/reserve] | $[X] |

**Total identified savings:** $[X,XXX]/month

## Key Insights

- [2-3 bullet points on what stands out]
```

Adapt the template to the user's question — don't generate all sections if they only asked about top services.

## Constraints

- Never fabricate cost figures. All numbers must come from tool/CLI output
- Use AmortizedCost by default (includes RI/SP amortization) unless user asks for UnblendedCost
- Dates: use ISO format (YYYY-MM-DD). Current month = 1st of month to today
- If a tool returns empty data, say so honestly — don't invent numbers
