---
name: eks-operation-review
description: "Run a structured EKS operational excellence assessment against a live cluster. Covers 10 areas — cluster lifecycle, IaC/GitOps, access & identity, observability, workload config, networking, autoscaling, deployment practices, operational processes, and add-on management — and produces a GREEN/AMBER/RED rated report with prioritized recommendations. Three paths: (1) EKS MCP tools via awslabs.eks-mcp-server (preferred); (2) direct AWS CLI + kubectl; (3) delegation to a coding agent. Activate for any request to audit, review, health-check, or score an EKS cluster's operational posture, including section-scoped reviews of individual areas. Not for upgrade readiness, cluster discovery, or architectural design advice."
argument-hint: "[which cluster / which area? e.g. 'review my EKS cluster', 'check EKS networking', 'score RBAC on my cluster']"
allowed-tools: Bash, Write, Read, Glob, Grep
user-invocable: true
---

# EKS Operation Review

This skill performs a structured 10-section operational assessment of a live EKS cluster, producing a GREEN/AMBER/RED rated report with prioritized recommendations. Checks are informed by the [EKS Best Practices Guide](https://docs.aws.amazon.com/eks/latest/best-practices/) and [EKS User Guide](https://docs.aws.amazon.com/eks/latest/userguide/). All operations are **read-only**.

## When to use

Activate for any request to audit, review, health-check, or score an EKS cluster's operational posture — including section-scoped reviews of individual areas (e.g., "check my EKS networking", "review RBAC on my cluster", "is my EKS cluster following best practices", "EKS operational health check").

Not for: upgrade readiness assessments, cluster discovery, or architectural design advice. General Kubernetes questions, AWS troubleshooting, cluster creation, and one-off kubectl commands should be handled directly without this skill.

## Routing — read first, before anything else

This skill works in three environments. Decide which path you are on **before** you start. The decisive signal at each step is what is reachable from the current environment — not user preference.

```
Are EKS MCP tools available (list_k8s_resources, describe_eks_resource,
list_eks_resources, get_eks_insights, ...)?
├── Yes → Path M (use awslabs.eks-mcp-server tools — preferred, richest cluster access)
└── No
    ├── Can you run `aws eks list-clusters` AND `kubectl`?
    │   ├── Yes → Path A (AWS CLI + kubectl directly)
    │   └── No
    │       ├── Is a coding agent enabled? → Path B (delegate)
    │       └── No → Stop. Tell the user: "I can't reach an EKS cluster from
    │                    here. Enable the awslabs.eks-mcp-server MCP server
    │                    (see .kiro/settings/mcp.json) and ensure AWS
    │                    credentials + Kubernetes RBAC access, or run in a
    │                    shell-capable agent with kubectl."
```

### Path M — EKS MCP tools (preferred)

The `awslabs.eks-mcp-server` exposes the cluster-state tools the assessment relies on — `list_eks_resources`, `describe_eks_resource`, `list_k8s_resources`, `read_k8s_resource`, `get_eks_insights`, `get_eks_vpc_config`, `get_cloudwatch_logs`/`get_cloudwatch_metrics`, `get_policies_for_role`, etc. Use these directly. AWS documentation lookups (when needed) use `awslabs.aws-documentation-mcp-server`. Both are declared in `.kiro/settings/mcp.json`.

Follow the workflow in `reference/workflow.md`. Per its tool-usage rules: do NOT call any tool on activation, discover clusters first (`aws eks list-clusters`), never use `manage_eks_stacks` for discovery, and do NOT retry a failed MCP call more than once.

### Path A — AWS CLI + kubectl

If the MCP tools aren't available but the AWS CLI and `kubectl` are reachable, run the same checks yourself: `aws eks list-clusters` / `describe-cluster` / `describe-addon` / `list-insights` for the AWS control-plane surface, and `kubectl get/describe` for the Kubernetes-level checks each steering file enumerates. Raw CLI output won't be pre-enriched, so assess each item against the steering rubric manually.

### Path B — Delegate

In a sandboxed host that cannot reach AWS or the MCP server but has a coding agent with shell + AWS/kubectl access, hand the coding agent this brief:
> "Run an EKS operational excellence assessment for the user's selected cluster by following the skill specification on disk at `skills/eks-operation-review/SKILL.md` and `skills/eks-operation-review/reference/workflow.md`. Execute Path A (AWS CLI + kubectl). Load each steering file from `skills/eks-operation-review/steering/` before its section. Read-only only. Produce the rated report per `steering/report-generation.md` and save it to `reports/`. Return the report path and the top findings by severity."

Relay only the coding agent's final summary back to the user.

## Instructions

Read and follow `reference/workflow.md` — it contains the full workflow (pre-flight, sections 1–10, report generation), tool-usage rules, the scenario→steering-file map, the rating rubric, and the report format. Load each steering file from `skills/eks-operation-review/steering/` before running its corresponding section.

## Prerequisites

- AWS credentials with EKS **read** access (`aws sts get-caller-identity` succeeds)
- Kubernetes RBAC read access to the target cluster (EKS access entry or `aws-auth`)
- Python 3.10+ and uv installed (to run the EKS MCP server)
- EKS MCP servers configured for your IDE — see `reference/mcp-setup.md` (copy `reference/mcp.json.example` to `.kiro/settings/mcp.json`). Pinned: `awslabs.eks-mcp-server@0.1.28`, `awslabs.aws-documentation-mcp-server@1.1.21`

## Constraints

- **Read-only.** Use only describe/list/get/read operations. Never call any AWS or Kubernetes API that mutates state (no Create*/Modify*/Delete*/Update*/Put*/apply/patch/scale).
- **Never fabricate findings, ratings, counts, versions, or cluster facts.** Every rating must be grounded in data actually returned by a tool/CLI call this run. If a check returns no data or fails, mark the item **UNKNOWN** — do not guess.
- **Discover, don't assume.** Always list clusters first; never hardcode or guess a cluster name.
- **Do not fabricate AWS documentation URLs.** Use only the pre-verified reference map in `steering/report-generation.md`; fall back to the section-level page when no specific URL matches.
- **Write reports only inside the workspace.** Output to the repo root or a `reports/` subfolder using the filename format `EKS-Operation-Review-<cluster>-<YYYY-MM-DD>-<HHMM>.md`. Never write outside the workspace.
- **One cluster at a time.** Re-run the skill for additional clusters.
- **Don't retry a failed MCP/CLI call more than once.** If it fails twice, stop and surface troubleshooting steps.
