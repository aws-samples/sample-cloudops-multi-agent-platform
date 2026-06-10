# Pricing agent — architecture, deploy modes, operations

End-to-end reference for the `pricing-agent`. Sibling of
`cost-operations-agent` under `finops-agent`. Handles the AWS
pricing **catalog**, anomaly detection, and budget status — NOT
actual spending (that's cost-operations-agent).

---

## 1. What the feature does

Three distinct surfaces rolled into one leaf:

- **AWS Pricing API catalog** — per-service, per-SKU on-demand
  pricing. Used to estimate cost for a proposed workload or to
  answer "what does an m5.xlarge in us-east-1 cost?".
- **Cost Anomaly Detection** — surfaces unusual spending patterns
  already detected by AWS Cost Anomaly Detection.
- **AWS Budgets** — budget utilization and forecasted-vs-actual
  status.

Representative prompts:

- `"What's the hourly price of an m5.xlarge in us-east-1 Linux?"`
- `"List valid instance types for Amazon RDS."`
- `"Any cost anomalies this month?"`
- `"How are my budgets trending?"`
- `"What's my account ID?"`

Behavior:

```
"What does an m5.xlarge cost in us-east-1?"
  → supervisor → finops-agent → pricing-agent
    → pricing___get_service_pricing(AmazonEC2, filters=...)
    → exact per-unit USD rate from AWS catalog
```

The agent prompt is explicit that it does NOT handle actual spend —
"how much did I spend" questions are routed by the supervisor to
`cost-operations-agent`. Pricing data is forward-looking; spending
data is historical.

---

## 2. Deploy modes — one mode

The Pricing API is a **publicly-readable API**; any AWS-authenticated
identity can call it. Anomaly detection and budgets read from the
caller's account. No cross-account role is needed.

- **What works:** everything, regardless of which account cloudops
  runs in.
- **Terraform:** no extra configuration. This is the one agent with
  no deploy-mode branching.

**Us-east-1 only.** The AWS Pricing API only has a us-east-1 endpoint
(same as Cost Optimization Hub). The Lambda hardcodes
`region_name="us-east-1"`. Do NOT set `AWS_REGION` elsewhere in the
Lambda — it's a reserved key Lambda injects itself.

---

## 3. Anomaly detection + Budgets

Both read from the caller's account:

- `ce:GetAnomalies` surfaces anomalies that AWS Cost Anomaly
  Detection has already identified. If you haven't configured
  anomaly monitors in the AWS Billing console, the tool returns
  zero anomalies — not an error, just empty. Honest and correct.
- `budgets:DescribeBudgets` + `budgets:ViewBudget` returns any
  budgets configured on the caller's account. Same empty-means-empty
  behavior if nothing is set up.

For cross-account / org-wide anomaly and budget visibility, you
need separate anomaly monitors / budgets configured in each account
(or in the payer account against consolidated data). That's an AWS
Billing console setup — out of scope for this agent.

---

## 4. Data model — what tools return

### `list_services`

```
{
  "services": [
    {"ServiceCode": "AmazonEC2", "AttributeNames": ["location", "instanceType", ...]},
    {"ServiceCode": "AmazonRDS", "AttributeNames": [...]}
  ],
  "count": 50
}
```

### `get_service_pricing`

```
{
  "service_code": "AmazonEC2",
  "filters_applied": {"regionCode": "us-east-1", "instanceType": "m5.xlarge"},
  "products": [
    {
      "sku": "...",
      "product_family": "Compute Instance",
      "attributes": {"location": "US East (N. Virginia)", "instanceType": "m5.xlarge", ...},
      "price_per_unit": {"USD": "0.192"},
      "unit": "Hrs",
      "effective_date": "2024-01-01"
    }
  ],
  "count": 5
}
```

### `get_anomalies`

```
{
  "anomalies": [
    {
      "AnomalyId": "...",
      "AnomalyStartDate": "2026-04-15",
      "AnomalyEndDate": "2026-04-18",
      "TotalImpact": {"ActualSpend": 245.12, "ExpectedSpend": 28.00, "Impact": 217.12},
      "RootCauses": [{"Service": "AmazonEC2", "Region": "us-east-1", ...}]
    }
  ],
  "count": 1
}
```

### `get_billing_alerts`

```
{
  "budgets": [
    {"BudgetName": "...", "BudgetLimit": 5000, "ActualSpend": 2995.88,
     "ForecastedSpend": 4800.00, "Utilization": "60%"}
  ],
  "count": 3
}
```

---

## 5. Known gotchas

| Symptom | Likely cause | Fix |
|---|---|---|
| Pricing API returns empty products | Filter combination is too narrow or uses invalid attribute name | Call `get_attribute_values(service_code, attribute)` first to discover valid values |
| All attribute names appear case-sensitive | AWS Pricing API IS case-sensitive (e.g. `regionCode` not `regioncode`) | Use the exact casing returned by `list_services[].AttributeNames` |
| No anomalies returned, but you know there are cost spikes | AWS Cost Anomaly Detection is not configured to monitor those services | Set up an anomaly monitor in AWS Billing console — this agent reads existing monitors, doesn't create them |
| Budget returns empty on a payer account | Budgets are per-account; consolidated-billing totals need separate org-level budgets | Configure the budgets you want to track explicitly |
| "Unknown service code `Ec2`" | Service codes use the "Amazon" / "AWS" prefix convention | Use `AmazonEC2`, `AmazonRDS`, `AWSLambda` — `list_services` is the source of truth |
| Pricing doesn't match my bill | The Pricing API shows list/on-demand rates; your bill reflects Savings Plans, RIs, committed discounts | Use `cost-operations-agent` for actual spend; use this agent for catalog lookups |
