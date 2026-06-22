# EKS MCP server setup

The EKS Operation Review skill's preferred execution path (Path M) uses two MCP
servers. This file explains how to make them available to your IDE.

## Servers used

| Server | Purpose | Pinned version |
|--------|---------|----------------|
| `awslabs.eks-mcp-server` | Live EKS cluster + Kubernetes resource access | `0.1.28` |
| `awslabs.aws-documentation-mcp-server` | AWS documentation lookups during assessment | `1.1.21` |

Versions are **pinned** to keep behaviour reproducible and avoid pulling
unreviewed upstream updates. To upgrade, bump the version strings after
reviewing the upstream changelog at <https://github.com/awslabs/mcp/releases>.

## Install (Kiro)

Kiro merges MCP config from the user level (`~/.kiro/settings/mcp.json`) and the
workspace level (`.kiro/settings/mcp.json`), with workspace taking precedence.

Copy the bundled example into your workspace settings:

```bash
mkdir -p .kiro/settings
cp skills/eks-operation-review/reference/mcp.json.example .kiro/settings/mcp.json
```

> `.kiro/settings/` holds machine-local settings and is intentionally NOT
> committed to this repository — keep your real `mcp.json` out of version
> control. The committed artifact is the `.example` template only.

Alternatively, add the two servers via Kiro's **Settings → MCP Servers** UI using
the same `command` / `args` / `env` shown in `mcp.json.example`.

## Install (Claude Code)

Add the two servers to `.mcp.json` at the project root (same JSON shape as
`mcp.json.example`). Claude Code will prompt to enable them on next launch.

## Prerequisites for the servers

- Python 3.10+ and [uv](https://docs.astral.sh/uv/getting-started/installation/)
  installed (`uvx` runs the pinned server packages on demand).
- Working AWS credentials — `aws sts get-caller-identity` must succeed. The
  servers read `AWS_PROFILE` / `AWS_REGION` from the environment; set them in the
  `env` block of `mcp.json` if you need a specific profile or region.
- Kubernetes RBAC read access to the target cluster (EKS access entry or
  `aws-auth` ConfigMap).

## Verify

Do NOT treat editing the config as a verification step. Confirm the server works
by making one live tool call — e.g. discover clusters with
`aws eks list-clusters`, then call `list_k8s_resources` against a discovered
cluster. If a call fails, see the troubleshooting steps in
`reference/workflow.md`.
