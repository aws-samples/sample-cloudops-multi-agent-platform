# EKS Operation Review — Workflow

> Loaded by `skills/eks-operation-review/SKILL.md`. This file contains the full
> assessment workflow, tool-usage rules, steering-file map, rating rubric, and
> report format. All paths below are relative to the repository root.

## Tool Usage Rules

1. **Do NOT call any tools when this skill is first activated.** Wait for the user to explicitly ask for a review.
2. **Do NOT read mcp.json or config files as a "check".** The only way to verify the MCP server works is to call an actual tool.
3. **Do NOT hardcode or guess cluster names.** Always discover clusters by listing them first.
4. **Do NOT retry a failed MCP tool call more than once.** If it fails twice, stop and show troubleshooting steps.
5. **Always load the relevant steering file before executing checks for that section.**
6. **Do NOT use the `manage_eks_stacks` MCP tool for cluster discovery or description.** That tool manages CloudFormation stacks, not EKS clusters. Use `aws eks list-clusters` and `aws eks describe-cluster` via Bash for pre-flight, and `list_k8s_resources` for Kubernetes checks.

## Steering File Loading

Before executing checks for any section, read the corresponding steering file from
`skills/eks-operation-review/steering/` using the Read tool.

### Scenario to Steering File Map

| User Request | Steering File(s) to Load |
|---|---|
| Full review / assess / audit / health check | ALL files in order: cluster-lifecycle -> addon-management, then report-generation |
| Upgrade / version / deprecated API | `skills/eks-operation-review/steering/cluster-lifecycle.md` |
| IRSA / RBAC / access / pod identity / endpoint | `skills/eks-operation-review/steering/access-identity.md` |
| Logging / metrics / alerting / observability | `skills/eks-operation-review/steering/observability.md` |
| Resource requests / probes / PDB / image tags / storage | `skills/eks-operation-review/steering/workload-configuration.md` |
| IP / subnet / DNS / CoreDNS / network policy | `skills/eks-operation-review/steering/networking.md` |
| Autoscaling / Karpenter / HPA / topology spread | `skills/eks-operation-review/steering/autoscaling.md` |
| Deployment / rollout / CI/CD / graceful shutdown | `skills/eks-operation-review/steering/deployment-practices.md` |
| Runbook / on-call / backup / DR / Velero | `skills/eks-operation-review/steering/operational-processes.md` |
| Add-on / node monitoring / cluster insights | `skills/eks-operation-review/steering/addon-management.md` |
| Generate / write report | `skills/eks-operation-review/steering/report-generation.md` |
| IaC / GitOps / ArgoCD / Flux / drift | `skills/eks-operation-review/steering/infrastructure-as-code.md` |

---

## Overview

This skill assesses your live EKS cluster against 10 areas of operational best practice. It connects to your cluster via the EKS MCP server, runs automated checks, rates each item as GREEN/AMBER/RED, and produces a report with prioritized recommendations and AWS documentation links.

## What Gets Assessed

| # | Section | Key Checks |
|---|---------|------------|
| 01 | Cluster Lifecycle & Upgrades | Version currency, data plane alignment, deprecated APIs, add-on compatibility, upgrade process |
| 02 | Infrastructure as Code & GitOps | IaC provenance, GitOps tools, drift detection, RBAC in code |
| 03 | Access & Identity | IRSA/Pod Identity, least privilege RBAC, API server endpoint security, Pod Security Admission |
| 04 | Observability | Control plane logging, metrics stack, log aggregation, alerting |
| 05 | Workload Configuration | Resource requests/limits, health probes, PDBs, image tags, storage |
| 06 | Networking | IP capacity, CoreDNS health, network policies |
| 07 | Autoscaling | Cluster autoscaler/Karpenter, HPA, topology spread |
| 08 | Deployment Practices | Rollout strategy, CI/CD, graceful shutdown |
| 09 | Operational Processes | Backup/DR, tool presence (Velero, AWS Backup) |
| 10 | Add-on Management | Managed add-ons, node health monitoring, cluster insights |

~70-75% of items are fully automatable. Items that require human knowledge (runbooks, on-call processes) are marked UNKNOWN with suggestions for what to investigate.

## Prerequisites

1. **AWS credentials configured** -- `aws configure` or `~/.aws/credentials` with a profile that has EKS access
2. **Python 3.10+** and **uv** installed ([Install uv](https://docs.astral.sh/uv/getting-started/installation/)) -- required to run the EKS MCP server
3. **Required AWS Permissions**:
   - `eks:Describe*`, `eks:List*`
   - `ec2:DescribeSubnets`, `ec2:DescribeSecurityGroupRules`
   - `ecr:DescribeRepositories`
   - `iam:ListAttachedRolePolicies`, `iam:ListRolePolicies`
   - `logs:DescribeLogGroups`
   - `cloudwatch:DescribeAlarms`
   - `backup:ListBackupPlans` (optional)

## MCP Server Configuration

The MCP servers use your existing AWS credentials from the environment (`~/.aws/credentials`, `AWS_PROFILE`, `AWS_REGION`). No additional configuration is needed if your AWS CLI already works.

The EKS MCP servers for this workspace are declared in `.kiro/settings/mcp.json`. If you
need to use a specific profile or region, update the EKS MCP server's `env` block there:

```json
"env": {
  "AWS_PROFILE": "your-profile-name",
  "AWS_REGION": "your-region",
  "FASTMCP_LOG_LEVEL": "ERROR"
}
```

## Getting Started

Run: `/eks-operation-review`

The skill will automatically discover your clusters and walk you through the assessment.

---

## Assessment Workflow

### Step 0: Pre-flight

This step verifies everything works before starting the assessment. Follow this exact sequence:

**Action 1 -- List clusters (discovers clusters)**

Use the AWS CLI to list clusters. Do NOT use the `manage_eks_stacks` MCP tool — it manages CloudFormation stacks, not cluster discovery.

```
aws eks list-clusters --output json
```

- Success -> Show the cluster list. Ask the user which cluster to assess. If only one cluster, confirm: "I found one cluster: [name]. Shall I assess this one?"
- Failure -> STOP. Do NOT retry more than once. Do NOT read config files. Show:

> **Cannot list EKS clusters.** Try these steps:
> 1. Check that AWS credentials work: `aws sts get-caller-identity`
> 2. Check the region: `aws configure get region`
> 3. Verify AWS_PROFILE and AWS_REGION in `.kiro/settings/mcp.json`

Wait for the user to resolve the issue.

**Action 2 -- Describe the selected cluster**

Use the AWS CLI to describe the cluster. Do NOT use `manage_eks_stacks` — it looks for CloudFormation stacks, not EKS clusters.

```
aws eks describe-cluster --name <cluster-name> --output json
```

From the response, show:
- Cluster name, Kubernetes version, platform version, region, status
- AWS account ID
- Authentication mode

**Action 3 -- Verify MCP connectivity**

Call one MCP tool to confirm the EKS MCP server works (e.g., list Nodes):

```
list_k8s_resources(cluster_name="<cluster-name>", kind="Node", api_version="v1")
```

- Success -> MCP server is working. Proceed.
- Failure -> STOP. Show:

> **The EKS MCP server isn't responding.** Try these steps:
> 1. Check that Python 3.10+ and uv are installed: `uv --version`
> 2. Test the MCP server: `uvx awslabs.eks-mcp-server@0.1.28`
> 3. Verify AWS_PROFILE and AWS_REGION in `.kiro/settings/mcp.json`

Wait for the user to resolve the issue.

**Action 4 -- Confirm**

Ask: *"Ready to start the assessment on [cluster-name] (v[version])?"*

Proceed only after the user confirms.

### Steps 1-10: Run Assessment

Read each steering file in section order using the Read tool. For each section:
1. Read the steering file from `skills/eks-operation-review/steering/`
2. Execute the checks described in it
3. Rate each item using the rubric below

**Error recovery:** If a section fails entirely (MCP server unreachable, permissions denied for all checks in that section, or repeated timeouts), mark all items in that section as UNKNOWN with a note explaining the failure reason, then proceed to the next section. Do not let one failed section block the rest of the assessment.

### Step 11: Generate Report

Read `skills/eks-operation-review/steering/report-generation.md` and produce the report.

---

## Rating Rubric

| Rating | Meaning |
|--------|---------|
| GREEN | Fully implemented -- matches EKS best practices |
| AMBER | Partial or inconsistent -- improvement opportunity |
| RED | Not implemented or significant gap -- action needed |
| UNKNOWN | Cannot be determined from cluster data -- investigate manually |

### Rules

- Only rate based on what was actually observed -- never assume
- If a check fails or returns no data, mark UNKNOWN
- Prioritize by blast radius: security > availability > cost
- Every RED finding must have a specific, actionable recommendation

---

## Report Format

### Consistency Rules (MANDATORY)

1. **Ratings must be consistent across the entire report.** If 4.1 is RED in the findings table, it must appear as RED everywhere -- executive summary, prioritized actions, quick wins.
2. **Prioritized Actions must reference the finding ID.** Write "4.1 -- Control Plane Logging RED" not just "Enable logging".
3. **Every RED must appear in Critical or Important.** Every AMBER must appear in Important or Quick Wins. Nothing rated RED/AMBER can be missing from Prioritized Actions.
4. **Executive Summary must match the findings.** Do not call something a "critical gap" if it's AMBER, or skip a RED item.

### File Output

- **Location:** Workspace root or `reports/` subfolder. Do NOT write outside the workspace.
- **Filename:** `EKS-Operation-Review-<cluster-name>-<YYYY-MM-DD>-<HHMM>.md`

### Template

```markdown
# EKS Operation Review Report
Cluster: [name] | Region: [region] | Version: [version]
Date: [YYYY-MM-DD HH:MM]

## Executive Summary
[2-3 paragraphs. Strengths first, then gaps. Every rating mentioned must match the findings tables.]

## Maturity Score
| Rating | Count | Percentage |
|--------|-------|------------|
| GREEN | X | X% |
| AMBER | X | X% |
| RED | X | X% |
| UNKNOWN | X | -- |

## Findings

### Section 01 -- Cluster Lifecycle & Upgrades
| Item | Status | Current State | Recommendation | References |
|------|--------|---------------|----------------|------------|

[Repeat for all 10 sections]

## Prioritized Actions

### Critical (Address within 30 days)
| # | Finding | Action | References |
|---|---------|--------|------------|
| 1 | [X.X -- Item Name] RED | [specific action] | [links] |

### Important (Address within 90 days)
| # | Finding | Action | References |
|---|---------|--------|------------|
| 1 | [X.X -- Item Name] AMBER | [specific action] | [links] |

### Quick Wins
| # | Finding | Action | Effort | Impact | References |
|---|---------|--------|--------|--------|------------|
| 1 | [X.X -- Item Name] | [action] | [estimate] | [what improves] | [links] |

## Items to Investigate Manually
[UNKNOWN items with specific questions to answer]

## AWS Reference Links
[All links grouped by topic]

---

*This report was generated by a Claude Code / Kiro skill provided as sample code for educational and demonstration purposes only. Findings should be reviewed and validated before acting on them. See the project's README and LICENSE for full terms.*
```

### AWS References

Use the pre-verified reference map in `skills/eks-operation-review/steering/report-generation.md` Step 7. Do NOT call the AWS Documentation MCP server during report generation — it adds latency and token cost. All URLs are pre-verified and mapped by section.

Do NOT fabricate URLs beyond the reference map. If a finding doesn't match a specific URL, use the fallback section-level page.

### HTML Conversion

If the user wants HTML output, run the bundled converter — do NOT generate HTML by hand:

```bash
python3 skills/eks-operation-review/tools/report_to_html.py <report-filename>.md
```
