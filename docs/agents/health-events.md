# AWS Health events — architecture, deploy modes, operations

This is the end-to-end reference for the health-events feature. Read the
**Deploy modes** section first to pick your architecture; the rest is
reference material.

---

## 1. What the feature does

Ingests AWS Health events (scheduled maintenance, service issues, account
notifications, AWS investigations) into DynamoDB so the
`health-events-agent` can answer questions like "what critical issues are
open?", "what's coming up in the next week?", "which accounts are
affected?" without hitting the AWS Health API on every query.

**What you see as a user:**
```
"Show me all CRITICAL health events from the last 7 days."
  → supervisor → ops-excellence-agent → health-events-agent
    → get_critical_events tool → DynamoDB query
    → response with impactSummary / remediationHint / affected resources
```

**What happens behind the scenes at ingest:**
```
AWS Health API
    │ (events published)
    ▼
EventBridge rule `source=aws.health`          ← no-cost delivery channel
    │
    ▼
SQS queue (14-day retention, DLQ-backed)      ← buffer for Lambda burst protection
    │
    ▼
Collector Lambda (cloudops-health-events-collector)
    │
    ├── Rules-based risk assessment (deterministic)
    │   Inputs: eventTypeCategory, statusCode, eventScopeCode, service,
    │   startTime-distance-to-now.
    │   Output: riskLevel ∈ {CRITICAL, HIGH, MEDIUM, LOW}
    │
    ├── LLM narrative enrichment (Claude Haiku 4.5, best-effort)
    │   Inputs: description, affected entities, event metadata.
    │   Output: impactSummary (<=140 chars), remediationHint (<=200 chars),
    │   affectedResourceTypes (list of normalised tokens).
    │   If Bedrock fails or enrichment is disabled, the row is still written
    │   WITHOUT these fields — ingest never blocks on LLM availability.
    │
    └── Cross-account resolution (optional)
        When CROSS_ACCOUNT_ROLE_ARN_HEALTH is set, the collector assumes that
        role before calling Organizations / Health org-view APIs. Otherwise
        uses its own execution role.
    │
    ▼
DynamoDB table cloudops-health-events
    PK: eventArn, SK: accountId       ← one row per (event, affected account)
    GSI CategoryTimeIndex             ← for get_critical_events, get_recent_events
    GSI AccountTimeIndex              ← for get_events_by_account
    TTL: 180 days                     ← auto-delete old events
```

---

## 2. Deploy modes — pick one

The right architecture depends on your AWS Organizations setup and which
account runs the cloudops stack. Use this decision tree.

```
Do you have an AWS Organization?
├── No → SINGLE ACCOUNT (Mode A)
│
└── Yes: which account runs the cloudops stack?
    ├── Management (payer) account → ORG VIEW, MGMT-HOSTED (Mode B)
    │
    ├── Dedicated ops/observability account
    │   with Health delegated-admin     → ORG VIEW, DELEGATED (Mode C)  ← recommended
    │
    └── Some other member account
        without any delegated-admin     → SINGLE ACCOUNT + CROSS-ACCOUNT (Mode D)
```

### Mode A — single account

You're on a standalone account or just want visibility into the account
cloudops runs in. Nothing special to configure.

* **What works:** EventBridge delivers aws.health events for this account.
  Collector stores them, agent can query. No support-plan requirement for
  the real-time path.
* **What doesn't:** Events for other accounts (you can't see them — the
  EventBridge rule only fires on this account's events). Backfill (needs
  Business+ Support on this account's Health API).
* **Terraform:** default. Nothing to set.

### Mode B — org view, mgmt-hosted

cloudops runs in the management (payer) account. Org view is enabled
directly on the mgmt account with no delegation.

* **What works:** EventBridge in the mgmt account receives aws.health
  events for **every member account** in real time. Collector fans out
  per-account rows to DynamoDB. Agent can answer per-account questions.
  Backfill with `--org` works against all members.
* **What doesn't:** Running the mgmt account as day-to-day ops is
  considered bad practice — this mode only makes sense for small orgs or
  testbed setups.
* **Bring-up — YOU run these from the management account. cloudops does
  not automate organization-level config changes.** These are one-time
  governance actions; the cloudops deploy has no business reaching into
  your management account with its own credentials to flip org-wide
  toggles.
  1. Turn on Health Organizational View on your Organization:
     ```bash
     AWS_PROFILE=<mgmt-profile> aws organizations \
       enable-aws-service-access \
       --service-principal health.amazonaws.com
     ```
     Reverse with `disable-aws-service-access` if you ever want to back
     out.
  2. Deploy the main cloudops stack in the mgmt account as usual
     (`make deploy-auto`). The EventBridge rule will pick up org-wide
     events now that org-view is on.
  3. Populate history: `make backfill-health DAYS=90 ORG=1`.

### Mode C — org view, delegated (recommended)

cloudops runs in a dedicated ops account. That account is registered as
the Health delegated administrator for the Organization. This matches how
Cost Explorer / Cost Optimization Hub delegated-admin works.

* **What works:** Everything in Mode B, plus: day-to-day ops runs with
  non-mgmt credentials. Safer blast radius. Single EventBridge rule in the
  ops account fans in the whole org. No cross-account AssumeRole needed at
  runtime — the delegated-admin account has native permission to call
  Organizations and Health org APIs.
* **Bring-up — YOU run these from the management account.** The two
  commands below are one-time Organizations-level changes that belong to
  your mgmt-account governance, not to the cloudops deploy. Own the
  configuration, own the teardown.
  1. Turn on Health Organizational View:
     ```bash
     AWS_PROFILE=<mgmt-profile> aws organizations \
       enable-aws-service-access \
       --service-principal health.amazonaws.com
     ```
  2. Register your ops account as the Health delegated administrator:
     ```bash
     AWS_PROFILE=<mgmt-profile> aws organizations \
       register-delegated-administrator \
       --account-id <OPS_ACCOUNT_12_DIGIT_ID> \
       --service-principal health.amazonaws.com
     ```
     Reverse with `deregister-delegated-administrator` if you ever want
     to back out.
  3. Deploy the main cloudops stack in the **ops** account as usual.
     Leave `health_events_cross_account_role_arn` empty in your
     `config.auto.tfvars.json` — the delegated-admin account has
     native org-scope permissions, so no AssumeRole is needed.
  4. Backfill from the ops account: `make backfill-health DAYS=90 ORG=1`.

### Mode D — single account + cross-account role (fallback)

cloudops runs in an ops account that is NOT the Health delegated admin.
The ops account holds the table + collector, but Organizations /
Health-org APIs must be called from the mgmt account. A cross-account role
in mgmt lets the collector assume into it for those specific calls.

* **When to pick this:** you already have cloudops deployed and don't want
  to take the delegated-admin step yet; or your org has policy reasons to
  keep Health as an uncategorized org-wide service.
* **What works:** account-name resolution via Organizations works (assumed
  role). Backfill with `--role-arn <arn>` works from the ops account.
  Real-time org-wide EventBridge ingest does NOT work in this mode — you
  only see the ops account's own events, since the EventBridge rule lives
  in that account.
* **Terraform steps:**
  1. Create an IAM role in the **mgmt** account that trusts the ops
     account's collector Lambda role. Grant it `organizations:DescribeAccount`
     and (optional) `health:Describe*ForOrganization`.
  2. Set `health_events_cross_account_role_arn =
     "arn:aws:iam::<MGMT>:role/CloudOpsHealthCrossAccount"` in the ops
     account's `config.auto.tfvars.json`.
  3. `make deploy-auto` in the ops account.
  4. Backfill from the ops account with the role explicitly:
     `make backfill-health DAYS=90 ROLE_ARN=arn:aws:iam::<MGMT>:role/CloudOpsHealthCrossAccount`

---

## 3. AWS Support plan matrix

AWS enforces these gates; our code just surfaces them cleanly.

| Capability                                          | Support plan needed        | Why |
|---                                                  |---                         |--- |
| EventBridge delivery of aws.health events           | **None — free**            | AWS pushes events; we receive |
| Real-time single-account ingest (Mode A)            | **None — free**            | Just EventBridge + Lambda |
| Real-time org-wide ingest (Modes B, C)              | **None — free**            | Org-view EventBridge is free once enabled |
| `health:DescribeEvents` (single-acct backfill)      | **Business+**              | API gate: `SubscriptionRequiredException` otherwise |
| `health:DescribeEventsForOrganization` (org backfill) | **Business+**            | Same gate |
| `EnableHealthServiceAccessForOrganization` (API)    | **Business+** via API      | Can be done on any plan via the console |
| `Organizations:RegisterDelegatedAdministrator`      | Any plan                   | Standard org admin action |
| `Organizations:DescribeAccount` (account-name resolve) | Any plan (Org resource) | Run from mgmt OR via cross-acct role |

**Our scripts fail cleanly when the plan gate is hit.** Running
`make backfill-health` on a Developer-Support account returns a clear
error pointing at the support-plan docs; no silent silent degradation.

---

## 4. Data model

### DynamoDB rows

```
eventArn             (string, PK)
accountId            (string, SK)
accountName          (string)       ← resolved via Organizations API
service              (string)       ← EC2, RDS, LAMBDA, etc.
eventTypeCode        (string)       ← e.g. AWS_EC2_INSTANCE_RETIREMENT_SCHEDULED
eventTypeCategory    (string)       ← issue | scheduledChange | accountNotification | investigation
eventScopeCode       (string)       ← ACCOUNT_SPECIFIC | PUBLIC | NONE
region               (string)
statusCode           (string)       ← open | upcoming | closed
startTime            (string, ISO)
lastUpdateTime       (string, ISO)
description          (string, ≤2000 chars)
affectedResources    (string)       ← comma-joined entity values
riskLevel            (string)       ← CRITICAL | HIGH | MEDIUM | LOW  (rules-based, see §5)
impactSummary        (string, ≤140 chars, optional)    ← LLM-generated
remediationHint      (string, ≤200 chars, optional)    ← LLM-generated, omitted when nothing actionable
affectedResourceTypes (list<string>, ≤10 items, optional) ← LLM-generated, e.g. ["ec2-instance"]
ttl                  (number, Unix epoch)             ← 180 days from lastUpdate
collectedAt          (string, ISO)
```

### Indexes

| Index              | PK           | SK              | Used by                          |
|---                 |---           |---              |---                               |
| (base table)       | eventArn     | accountId       | `get_event_by_arn`               |
| `CategoryTimeIndex`| eventTypeCategory | lastUpdateTime | `get_critical_events`, `get_recent_events`, `get_event_summary` |
| `AccountTimeIndex` | accountId    | lastUpdateTime  | `get_events_by_account`, `get_health_events` (when account_id filter set) |

Service is NOT a key attribute — `get_events_by_service` scans with a
filter. Service cardinality is low (~20 values org-wide) so this is
acceptable at typical scale.

---

## 5. Risk assessment — the deterministic rules

Priority labels are assigned in `src/lambda/collectors/health-events/
handler.py::_assess_risk`. Pure function, no LLM involvement. The rules:

```
# Issues (eventTypeCategory="issue")
open + ACCOUNT_SPECIFIC                     → CRITICAL
open + core service (EC2/RDS/LAMBDA/ECS/EKS/DYNAMODB/S3) → CRITICAL
open + other service                        → HIGH
closed                                      → LOW
upcoming                                    → MEDIUM

# Investigations (eventTypeCategory="investigation")
open                                        → HIGH
closed                                      → MEDIUM

# Scheduled changes (eventTypeCategory="scheduledChange")
upcoming, ≤3d away, ACCOUNT_SPECIFIC        → HIGH
upcoming, ≤3d away                          → MEDIUM
upcoming, ≤14d away                         → MEDIUM
upcoming, distant                           → MEDIUM
closed                                      → LOW

# Account notifications
anything                                    → LOW
```

### Why these are deterministic and not LLM-assigned

LLM severity labelling on operational event streams benchmarks at 70–85%
F1 (see research in `temp/nr-migration/optimizations.md` if interested).
False CRITICAL on a benign notification, or false LOW on a real incident,
both cause operational harm. The rules above use only fields AWS publishes
(no inference) and are explainable in one line each.

LLMs are used for narrative fields (impactSummary, remediationHint,
affectedResourceTypes) where a wrong output degrades UX but can't mislead
triage decisions.

---

## 6. Operations

### First deploy

If the stack is freshly deployed, the EventBridge rule only catches events
from that moment forward. To populate history (up to 90 days, AWS Health's
retention limit):

```bash
make backfill-health DAYS=30           # Mode A or D (single-account)
make backfill-health DAYS=30 ORG=1     # Mode B or C (org-view)
```

Requires Business+ Support on the account holding the Health API endpoint
being called (see §3).

`deploy.sh` hints at the backfill command at the end of every `make
deploy-auto` when it detects an empty table.

### Disabling LLM enrichment

If you don't want to pay for Bedrock invocations (even at Haiku 4.5
prices), set the tfvar:

```hcl
# terraform/config.auto.tfvars.json
{
  "enrichment_model_id": ""
}
```

After the next `make deploy-auto`, the collector writes rules-based fields
only. Existing rows with enrichment fields are not affected — they retain
what was there.

### Monitoring

* Collector metrics: `AWS/Lambda → cloudops-health-events-collector` →
  Invocations, Errors, Duration.
* DLQ depth: `cloudops-health-events-dlq`. Non-zero means the collector
  hit an uncaught exception 3 times in a row on the same event.
* Enrichment token cost: collector logs include `Enrichment OK: in=N
  out=M cache_read=K` — grep for these in CloudWatch to track per-event
  Bedrock usage.

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Table empty after deploy | Just deployed; EventBridge only catches NEW events | `make backfill-health DAYS=30` |
| `SubscriptionRequiredException` from backfill | Account on Basic/Developer Support | Upgrade to Business+ or skip backfill |
| `impactSummary` missing on new rows | Bedrock call failed (check CloudWatch logs) | Non-fatal; event still stored. Investigate token budget or IAM |
| Events from other accounts not showing up | Not in org-view mode | Follow Mode B or C |
| Collector stuck after code change | Collector zip hash unchanged | `rm .lambda-hashes/collector-health-events.sha && make deploy-auto` |
| `Unknown tool: ...` from the agent | Gateway target schemas desynced | Rerun `make deploy-auto` (post-apply `sync_gateway_tools` re-uploads full schemas) |
