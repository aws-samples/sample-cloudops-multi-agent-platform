# Topology tests — Layer 2 (slice + build-script + `terraform validate`)

Run manually or on a non-default CI lane:

```bash
.venv/bin/pytest tests/topology/ -v
# or via marker from the unit suite layout
.venv/bin/pytest -m topology
```

**Cost to run: ~5–15 seconds.** No AWS creds. No `terraform plan`. No container builds.

## What this covers (that Layer 1 didn't)

Layer 1 (`tests/unit/test_topology.py`) validates `hierarchy.json` and `tools.json` as pure data. Layer 2 drops one level lower and exercises the **actual deploy-adjacent shell scripts and Terraform code** against mutated hierarchies:

- `scripts/lib/build.sh::_write_agent_hierarchy_slice` — the bash slicer (Layer 1 only checked a Python re-implementation).
- `scripts/lib/hierarchy.sh::_load_hierarchy` — the frontend-detection logic. Confirms that if you flip `type: "frontend"` to a different agent, `FRONTEND_AGENT` resolves to that agent (NOT the hardcoded `supervisor` default).
- `terraform validate` on the actual `terraform/` module tree. Catches syntax errors, bad module references, missing variables, wrong resource types. Does NOT catch provider-level issues (that needs `terraform plan`, which needs creds and is Layer 3).

## What this does NOT cover

- **No deploy**: no ECR pushes, no container builds, no `terraform apply`.
- **No runtime validation**: agent containers aren't exercised. That's Layer 3.
- **No UI tests**: the frontend Next.js routing isn't exercised. That's Layer 3.

## Layering recap

- **Layer 1** (`tests/unit/test_topology.py`): pure data validation of `hierarchy.json` + `tools.json`. Runs on every `make test`. Zero cost.
- **Layer 2** (here): shell scripts and `terraform validate`. Runs on every `make test`. Zero AWS cost.

A Layer 3 integration-deploy suite was prototyped during the 2026-05-03/04 topology-flexibility work but **removed** on 2026-05-04 because all three topology shapes (full, orchestrator-as-frontend, solo-leaf) were validated live on the dev stack instead — and the real platform bugs it caught (agent_type derivation, gateway IAM permission, report_enabled flag) are now regression-guarded by Layer 1 (`test_promoted_frontend_has_either_children_or_tools`) which runs in ~20ms. Layer 3's ~90-min / ~$5-per-run cost stopped pulling its weight once the static layer grew teeth. See the removed optimizations-file entry for a reconstruction outline if we ever want it back.
