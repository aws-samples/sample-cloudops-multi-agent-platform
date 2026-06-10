# Observability & Sampling Tuning

Guidance for setting X-Ray sampling + CloudWatch Transaction Search
indexing so the **built-in AWS Bedrock AgentCore GenAI Observability
dashboard** (and any future AgentCore Evaluations runs) see session
traces reliably.

This is account+region-scoped configuration that lives outside Terraform
(AWS console → X-Ray → Sampling / Transaction Search, or via the AWS
CLI). Terraform creates the delivery pipelines and the IAM permissions
runtimes need to export spans; it doesn't own sampling/indexing rules,
because those are account-wide settings shared across everything in the
region.

---

## Two knobs that matter

Both have to be set correctly for spans to reach the `aws/spans` log
group (which is what the GenAI dashboard + evaluators read from).

| Knob | What it controls | Where to set it | Default |
|---|---|---|---|
| **X-Ray sampling rule** (`FixedRate`) | % of agent-emitted spans actually exported to X-Ray | X-Ray console → Sampling → `Default` rule (or a scoped rule) | **5%** (0.05) |
| **X-Ray Transaction Search indexing rule** (`DesiredSamplingPercentage`) | % of exported spans indexed into `aws/spans` for search + evaluation | X-Ray console → Transaction Search → Indexing rules → `Default` | **1%** |

End-to-end rate of spans that land in `aws/spans` =
`FixedRate × DesiredSamplingPercentage`.

At the AWS defaults that's `5% × 1% = 0.05%` — one in 2,000 spans.

## Why defaults don't work for us

Every agent invocation produces ~15-30 spans (the supervisor's
event-loop span, one Bedrock model-invoke span, each delegated sub-agent
call, each MCP tool call). At 0.05% effective sampling, most sessions
land **zero** indexed spans, and the GenAI dashboard's per-session view
shows nothing. The symptom is easy to confuse with "tracing isn't
configured" — but the runtime-to-X-Ray delivery pipeline
(`terraform/modules/core/observability/main.tf`) is already correctly
wired. The gap is sampling.

Separately, the runtime execution role must grant
`xray:GetSamplingRules` + `xray:GetSamplingTargets` — the AWS
OpenTelemetry Distro calls these every ~10s to refresh the sampling
decision. Without them ADOT silently drops spans. Those permissions
live in the inline policies in
`terraform/modules/core/agent-runtime-base/main.tf` and
`terraform/modules/core/agentcore-runtime/main.tf`.

## Recommended configuration

For our expected load (a handful of users × ~100 queries/day, plus
occasional report generation runs producing ~30-50 spans each):

- **Sampling rate: 100%** — every span exported.
- **Transaction Search indexing: 100%** — every exported span searchable.

### Why this is the right call at our scale

**Load estimate:** ~1,000 queries/day × ~20 spans average =
~**600,000 spans/month**.

**Cost impact (us-east-1, approximate):**

- X-Ray trace ingestion: first 100K spans/month free; remaining 500K ×
  $5/million ≈ **$2.50/mo**
- `aws/spans` logs ingest: 600K records × ~2KB ≈ 1.2GB × $0.50/GB ≈
  **$0.60/mo**
- `aws/spans` logs storage: 1.2GB × $0.03/GB ≈ **$0.04/mo**
- **Total extra: ~$3-4/month**

Negligible against the value of reliable session-level traces and
ability to debug multi-agent delegation chains.

### Why not a middle ground

5% sampling × 100% indexing (~$1/mo) looks cheaper but defeats the
purpose — 95% of spans dropped before they reach X-Ray means most eval
sessions still lose their trace data. The sampling knob is the binding
constraint; splitting the difference yields cost savings that are
already tiny without fixing the reliability.

## When to revisit

| Trigger | Action |
|---|---|
| Load grows 10× (10,000 queries/day) | Revisit — cost climbs to $30-40/mo, still likely worth it |
| Multiple heavy workloads on the same AWS account+region | Switch from the blunt `Default` sampling rule to a service-scoped rule so only CloudOps runs at 100% (see below) |
| CloudWatch bill exceeds acceptable threshold | Drop indexing to 20-30% and accept some session flakiness, OR scope sampling to eval sessions only via `OTEL_TRACES_SAMPLER` env on the runtime (per-session override) |

## Service-scoped sampling (if needed later)

If other services in the account start emitting heavy trace data and
you don't want them at 100%, keep `Default` at 5% and add a
higher-priority rule scoped to CloudOps' X-Ray service name:

```
Rule name:      cloudops-full-sample
Priority:       100   (lower = evaluated first)
Fixed rate:     1.0   (100%)
Reservoir:      1
Service name:   <agent_name>         (e.g. supervisor, or a wildcard)
Service type:   *
Host:           *
HTTP method:    *
URL path:       *
Resource ARN:   *
```

Service name comes from `OTEL_SERVICE_NAME` set in
`src/agents/shared/agent_hierarchy.py` — one per agent, matching
`hierarchy.json` names.

## Applying the recommended settings

Via AWS CLI:

```bash
# Set sampling to 100%
aws xray update-sampling-rule \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --sampling-rule-update '{"RuleName":"Default","FixedRate":1.0}'

# Set Transaction Search indexing to 100%
aws xray update-indexing-rule \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --name Default \
  --rule '{"Probabilistic":{"DesiredSamplingPercentage":100.0}}'
```

Verify:

```bash
aws xray get-sampling-rules \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --query 'SamplingRuleRecords[?SamplingRule.RuleName==`Default`].SamplingRule.FixedRate'
aws xray get-indexing-rules \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

Propagation: sampling rule updates take a few minutes to reach ADOT
clients (they refresh via `GetSamplingTargets` every 10s by default).
Indexing rule is immediate.

## Verifying end-to-end after changes

Run one invocation, then:

```bash
# Any spans in aws/spans in the last 15 min?
aws logs filter-log-events \
  --log-group-name aws/spans \
  --start-time $(( ($(date +%s) - 900) * 1000 )) \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --query 'events[0:5].message' --output text | head
```

Expect non-empty output with JSON spans naming
`strands.telemetry.tracer`, `opentelemetry.instrumentation.botocore.bedrock-runtime`,
and `opentelemetry.instrumentation.starlette` scopes.

For a per-session check, query `aws/spans` filtered by session id — it
reports the scope breakdown for a specific session.
