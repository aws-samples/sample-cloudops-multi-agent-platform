---
name: tag-governance-assessment
description: "AWS Tag Governance assessment — tag compliance scoring, non-compliant resource identification, cost allocation tag status, and remediation guidance with Resource Explorer deep-links. Three paths: (1) MCP tools via deployed gateway (preferred); (2) direct AWS CLI; (3) delegation to coding agent. Use when the user asks about tag compliance, missing tags, tagging policy, untagged resources, or cost allocation tags."
argument-hint: "[what do you want to know? e.g. 'tag compliance summary', 'untagged resources', 'cost allocation tag status']"
allowed-tools: Bash, Write, Read, Glob, Grep
user-invocable: true
---

# AWS Tag Governance Assessment

Assess tag compliance across AWS resources — identify non-compliant resources, score compliance by service/account, check cost allocation tag activation, and provide remediation links. Output is a structured markdown report.

## Routing

```
Are tag-governance MCP tools available (check_tag_compliance, get_org_tag_compliance_summary, etc.)?
├── Yes → Path M (use MCP tools — handles cross-account, org-wide views)
└── No
    ├── Can you run `aws --version`?
    │   ├── Yes → Path A (AWS CLI — resourcegroupstaggingapi)
    │   └── No
    │       ├── Is a coding agent enabled? → Path B (delegate)
    │       └── No → Stop. Inform user to deploy gateway-only or use Claude Code/Kiro.
```

## Path M — MCP Tools (preferred)

| Tool | Use for |
|------|---------|
| `get_required_tags` | Resolve required tags from org tag policy or user input |
| `check_tag_compliance` | Scan resources and report compliance (per-service, per-account breakdown) |
| `get_org_tag_compliance_summary` | Aggregated compliance counts via AWS Organizations (org-wide) |
| `list_tag_keys_in_use` | All tag keys currently applied to resources |
| `find_untagged_resources` | Resources with zero tags (via Resource Explorer) |
| `list_cost_allocation_tag_status` | Which tags are activated for cost allocation billing |
| `get_remediation_guidance` | Resource Explorer deep-links for bulk-fixing non-compliant resources |

### Workflow

1. **Determine scope** — single account or org-wide? Specific services? Specific required tags?
2. **Resolve required tags** — call `get_required_tags` (reads from org tag policy if available, or ask user)
3. **Run compliance check** — `check_tag_compliance` for detailed per-resource results, or `get_org_tag_compliance_summary` for org-wide counts
4. **Identify gaps** — `find_untagged_resources` for resources with no tags at all
5. **Check cost allocation** — `list_cost_allocation_tag_status` to verify billing tags are activated
6. **Provide remediation** — `get_remediation_guidance` for Resource Explorer links to fix resources
7. **Format as report** using the output template below

### Example flows

**"How's my tag compliance?"**
→ `get_required_tags` → `check_tag_compliance` → format as summary + breakdown

**"What resources are untagged?"**
→ `find_untagged_resources` → table of resource types and counts

**"Are my cost allocation tags active?"**
→ `list_cost_allocation_tag_status` → table showing activated vs inactive tags

**"Give me remediation links for non-compliant resources"**
→ `check_tag_compliance` → `get_remediation_guidance` → deep-links by service

## Path A — AWS CLI

```bash
# Get tag compliance summary (org-wide, requires Organizations)
aws resourcegroupstaggingapi get-compliance-summary \
  --group-by TARGET_ID,RESOURCE_TYPE

# Get resources missing specific tags
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Environment,Values= \
  --query 'ResourceTagMappingList[?!Tags[?Key==`Environment`]]'

# List all tag keys in use
aws resourcegroupstaggingapi get-tag-keys

# Cost allocation tag status
aws ce list-cost-allocation-tags --status active
aws ce list-cost-allocation-tags --status inactive
```

## Path B — Delegate

Hand the coding agent:
> "Check AWS tag compliance using resourcegroupstaggingapi. Required tags: [list or 'from org policy']. Report compliance percentage by service, list top non-compliant resource types, and check cost allocation tag activation status."

## Output Template

```markdown
# Tag Governance Assessment

**Account:** [account ID or "Organization-wide"]
**Required Tags:** [list of required tag keys]
**Generated:** [timestamp]

## Compliance Score

| Metric | Value |
|--------|-------|
| Overall compliance | [X]% |
| Compliant resources | [n] / [total] |
| Non-compliant resources | [n] |
| Untagged resources (zero tags) | [n] |

## Compliance by Service

| Service | Total | Compliant | Non-Compliant | Compliance % |
|---------|-------|-----------|---------------|:---:|
| EC2 | [n] | [n] | [n] | [X]% |
| S3 | [n] | [n] | [n] | [X]% |
| RDS | [n] | [n] | [n] | [X]% |

## Top Non-Compliant Resources

| Resource Type | Count | Missing Tags |
|---------------|-------|-------------|
| [type] | [n] | [tag1, tag2] |

## Cost Allocation Tags

| Tag Key | Status | Billing Impact |
|---------|--------|---------------|
| Environment | ✅ Active | Cost visible by environment |
| Team | ❌ Inactive | Cost NOT broken down by team |

## Remediation

### Priority Fixes
1. **[Service] — [n] resources missing [tag]**
   - [Resource Explorer deep-link]
2. **[Service] — [n] resources missing [tag]**
   - [Resource Explorer deep-link]

### Recommendations
- Activate inactive cost allocation tags via Billing Console
- Add tag policy enforcement via AWS Organizations SCPs
- Consider auto-tagging via AWS Config rules or Tag Policies
```

## Constraints

- Never fabricate compliance numbers. All data must come from tool/CLI output
- Required tags: prefer org tag policy (if available) over hardcoded lists
- Two evaluation modes exist in `check_tag_compliance`: (1) in-Python evaluation against required tags, (2) AWS-native GetComplianceSummary. Use whichever the tool supports
- If cross-account role is not configured, results are single-account only — inform the user
- Resource Explorer deep-links require Resource Explorer to be enabled in the account
