"""Agent hierarchy map and dependency resolution.

Loads the agent tree from ``agents/hierarchy.json`` and provides a
pure-function resolver that expands any subset of agent names into the
full set required for a working deployment.

To add a new agent, edit ``agents/hierarchy.json`` — no Python changes needed.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_RUNTIME_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,47}$")

# ---------------------------------------------------------------------------
# Load hierarchy from JSON
# ---------------------------------------------------------------------------

_HIERARCHY_FILE = Path(__file__).resolve().parent.parent / "agents" / "hierarchy.json"


def _load_hierarchy(path: Path = _HIERARCHY_FILE) -> dict:
    """Load and return the raw hierarchy dict from JSON."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _build_maps(hierarchy: dict) -> tuple[
    dict[str, str],  # AGENT_PARENT
    dict[str, list[str]],  # AGENT_CHILDREN
    dict[str, str],  # AGENT_DIRS
    dict[str, str],  # AGENT_DESCRIPTIONS
]:
    """Derive parent, children, dirs, and descriptions maps from the hierarchy."""
    parent_map: dict[str, str] = {}
    children_map: dict[str, list[str]] = {}
    dirs_map: dict[str, str] = {}
    desc_map: dict[str, str] = {}

    for name, info in hierarchy.items():
        dirs_map[name] = info["dir"]
        desc_map[name] = info.get("description", "")
        children = info.get("children", [])
        if children:
            children_map[name] = children
            for child in children:
                parent_map[child] = name

    return parent_map, children_map, dirs_map, desc_map


# Build module-level constants from JSON
_hierarchy = _load_hierarchy()
AGENT_PARENT, AGENT_CHILDREN, AGENT_DIRS, AGENT_DESCRIPTIONS = _build_maps(_hierarchy)

ALL_AGENTS: set[str] = set(_hierarchy.keys())

MID_LEVEL_AGENTS: set[str] = {
    name for name, parent in AGENT_PARENT.items() if parent == "supervisor"
}

LEAF_AGENTS: set[str] = ALL_AGENTS - MID_LEVEL_AGENTS - {"supervisor"}


# ---------------------------------------------------------------------------
# Dependency resolution
# ---------------------------------------------------------------------------


def resolve_agents(deploy_agents: list[str]) -> set[str]:
    """Expand a list of agent names into the full deployment set.

    Rules:
        * Empty list → all agents.
        * Mid-level agent → supervisor + that agent + all its children.
        * Leaf agent → supervisor + its parent + that agent.
        * Supervisor is always included.
        * Unrecognised names are warned and skipped.
    """
    if not deploy_agents:
        return set(ALL_AGENTS)

    resolved: set[str] = {"supervisor"}

    for name in deploy_agents:
        if name == "supervisor":
            continue
        if name in MID_LEVEL_AGENTS:
            resolved.add(name)
            resolved.update(AGENT_CHILDREN.get(name, []))
        elif name in LEAF_AGENTS:
            resolved.add(name)
            parent = AGENT_PARENT.get(name)
            if parent:
                resolved.add(parent)
        else:
            logger.warning("Unrecognized agent name '%s', skipping", name)

    return resolved


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------


def ecr_repo_name(agent_name: str, project_prefix: str, environment: str) -> str:
    """Return the ECR repository name for an agent."""
    return f"{project_prefix}-{environment}-{agent_name}"


def runtime_name(agent_name: str, project_tag: str) -> str:
    """Return the AgentCore Runtime name for an agent.

    Raises:
        ValueError: If the result doesn't match the required regex.
    """
    name = f"{project_tag}_{agent_name.replace('-', '_')}_runtime"
    if not _RUNTIME_NAME_RE.match(name):
        raise ValueError(
            f"Runtime name '{name}' doesn't match '^[a-zA-Z][a-zA-Z0-9_]{{0,47}}$'"
        )
    return name


def endpoint_name(agent_name: str, project_tag: str) -> str:
    """Return the AgentCore Runtime endpoint name for an agent."""
    return f"{project_tag}_{agent_name.replace('-', '_')}_endpoint"


def agent_domain(agent_name: str) -> str:
    """Return the OTEL domain for an agent."""
    if agent_name == "supervisor":
        return "supervisor"
    if agent_name in MID_LEVEL_AGENTS:
        return agent_name.removesuffix("-agent")
    parent = AGENT_PARENT.get(agent_name, "")
    if parent and parent != "supervisor":
        return parent.removesuffix("-agent")
    return agent_name


def build_agent_env_vars(
    agent_name: str,
    registry_table: str,
    gateway_endpoint: str,
    memory_id: str = "",
) -> dict[str, str]:
    """Return the environment variable map for an agent's runtime."""
    domain = agent_domain(agent_name)
    env: dict[str, str] = {
        "AGENT_REGISTRY_TABLE": registry_table,
        "AGENTCORE_GATEWAY_ENDPOINT": gateway_endpoint,
        "OTEL_SERVICE_NAME": agent_name,
        "OTEL_RESOURCE_ATTRIBUTES": f"domain={domain},agent_name={agent_name}",
    }
    if (
        agent_name in MID_LEVEL_AGENTS
        and memory_id
        and memory_id != "placeholder-set-after-creation"
    ):
        env["AGENTCORE_MEMORY_ID"] = memory_id
    return env
