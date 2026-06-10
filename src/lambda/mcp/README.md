# Lambda MCP Tools

One Lambda per AWS API surface. Each Lambda can serve multiple tools, registered as AgentCore Gateway targets. `tools.json` is the single source of truth for schemas.

## Adding a tool

1. Create `<tool>/handler.py` + `requirements.txt`.
2. Add an entry to `tools.json`: handler path, IAM actions, `tools` array (each with `name`, `description`, `input_schema`), optional `env_vars` and `needs_*` flags.
3. Assign to an agent via the `tools` array in `src/agents/hierarchy.json` (worker agents only).
4. `make deploy-auto`.

## Handler pattern

```python
def handler(event, context):
    tool_name = context.client_context.custom["bedrockAgentCoreToolName"].split("___")[1]
    # event body contains tool params directly — NOT wrapped in {"action":..., "params":...}
    ...
```

- Entry point is `handler.handler` (NOT `lambda_function.lambda_handler`). Rename when copying reference code.
- Include a `get_today_date` tool (no params) so the model can resolve "last month" without guessing.

## The one gotcha that causes silent failures after every deploy

Terraform's `aws_bedrockagentcore_gateway_target` only supports ONE `inline_payload` tool schema per target. For multi-tool Lambdas, `terraform apply` overwrites the gateway target schemas with a single-tool placeholder. `scripts/deploy.sh` deletes `.lambda-hashes/gateway-tools.sha` immediately after apply so `sync_gateway_tools` re-uploads the full multi-tool schemas from `tools.json`.

If tools break after a deploy (model gets `"Unknown tool"` errors, or hallucinates fake tool calls):
1. Rerun `make deploy-auto` — the post-apply `sync_gateway_tools` step re-uploads schemas from `tools.json` (the `.lambda-hashes/gateway-tools.sha` invalidation forces it to run).
2. If that doesn't fix it: check that `input_schema` isn't an empty `{ "type": "object" }` — the model gets no parameter hints and sends `{}`.

## Env var wiring

Two sources, both resolved at deploy time:
- **Infra-derived**: wired automatically by Terraform when `needs_<module>: true` is set in `tools.json` (e.g., `needs_health_events`). Table names, ARNs, etc.
- **User-provided**: declared in `tools.json` `env_vars` with `$VAR` syntax (e.g., `"CUR_DATABASE_NAME": "$CUR_DATABASE_NAME"`). Resolved from `.env` by `generate_tfvars`. Plain values (no `$`) are defaults. Empty resolved values are omitted.

Infra-derived wins if both set the same key.

## AWS API regional quirks

- `cost-optimization-hub` API is **us-east-1 only**, regardless of deploy region. Lambda hardcodes `region_name="us-east-1"`. Always call `get_enrollment_status` first — if not opted in, other calls throw `AccessDeniedException`.
- `pricing` API is **us-east-1 only** as well (same hardcode pattern).
- Don't set `AWS_REGION` in Lambda `environment.variables` in Terraform — it's a reserved key and `CreateFunction` fails with `InvalidParameterValueException`. Lambda injects it automatically. (Runtimes are different — they MUST set `AWS_REGION` + `AWS_DEFAULT_REGION` explicitly because AgentCore doesn't inject them.)

## Cross-account access

`shared/cross_account.py` is packaged into every tool's zip at the root, so tool handlers can do `from shared.cross_account import get_aws_client` unconditionally (no try/except fallback needed).

Two usage patterns:

1. **Static target, known at deploy time.** Declare a role-ARN env var in `tools.json`:
   ```json
   "env_vars": {
     "CROSS_ACCOUNT_ROLE_ARN": "$CROSS_ACCOUNT_ROLE_ARN"
   }
   ```
   and use `get_aws_client("ce")` in the handler. If the env var is unset at deploy time, the Lambda uses its execution role. If set, Terraform (via `lambda-tool-base`) scopes `sts:AssumeRole` to that exact ARN. For a single Lambda that needs different roles for different services (e.g. CE in the payer, COH in a delegated admin), use aliased names: `CROSS_ACCOUNT_ROLE_ARN_COH`, and pass `role_alias="COH"` to `get_aws_client`.

2. **Dynamic spoke accounts, discovered at runtime.** Used by network-resilience where TGW attachments surface account IDs from the topology. Call `assume_role_for_account(account_id, "NetworkReadOnlyRole", service="ec2")`. ARN is built at call time; results are cached per `(account_id, role_name)`. Provision the role in spoke accounts via a CloudFormation StackSet over the Organization.

Both paths degrade gracefully — returning `None` for failed assumes so callers can skip the account and keep going.

Pricing is always local (no cross-account concept). Health-events stores data in the deployment account's DynamoDB table, but the **collector** (not the MCP tool — the EventBridge-triggered Lambda under `src/lambda/collectors/health-events/`) supports cross-account for Organizations/Health-org-view calls via `CROSS_ACCOUNT_ROLE_ARN_HEALTH` (alias `HEALTH`). See `docs/agents/health-events.md` for the four deploy modes (single-account / org-mgmt / org-delegated / cross-account).

## Packaging

`make package` hashes each tool dir (`.py`, `.txt`, `.json`) plus `shared/` and skips unchanged ones. Parallel subshells. To force a single tool repackage: `rm .lambda-hashes/<tool>.sha`. Changes to `shared/` invalidate every tool's hash (same pattern as `hierarchy.json` for agents).

Terraform `aws_lambda_function` MUST include `source_code_hash = filebase64sha256(...)` — without it, repackaged zips don't trigger code updates and old Lambda code keeps running. `filebase64sha256` evaluates during plan AND destroy — always `make package` before ANY terraform op (including destroy), or destroy fails on missing zip.
