"""Verify ``src/agents/hierarchy.json`` and ``src/lambda/mcp/tools.json`` are
wired correctly for the ``network-resiliency-agent`` leaf.

These are config-level invariants the platform's generic worker container
assumes at runtime. Breaking any of them causes silent 424s after deploy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_HIERARCHY = _REPO / "src" / "agents" / "hierarchy.json"
_TOOLS_JSON = _REPO / "src" / "lambda" / "mcp" / "tools.json"


@pytest.fixture(scope="module")
def hierarchy() -> dict:
    with _HIERARCHY.open() as f:
        return json.load(f)


@pytest.fixture(scope="module")
def tools_json() -> dict:
    with _TOOLS_JSON.open() as f:
        return json.load(f)


def test_leaf_entry_present(hierarchy: dict) -> None:
    assert "network-resiliency-agent" in hierarchy


def test_leaf_required_fields(hierarchy: dict) -> None:
    """Every field the generic worker container expects at runtime."""
    cfg = hierarchy["network-resiliency-agent"]
    for field in ("type", "dir", "protocol", "description", "model", "prompt", "tools"):
        assert field in cfg, f"missing required field: {field}"


def test_leaf_type_is_worker(hierarchy: dict) -> None:
    assert hierarchy["network-resiliency-agent"]["type"] == "worker"


def test_leaf_protocol_is_http(hierarchy: dict) -> None:
    """Every agent MUST be 'http' — AGUI for the frontend is handled post-deploy."""
    assert hierarchy["network-resiliency-agent"]["protocol"] == "http"


def test_leaf_dir_is_generic_worker(hierarchy: dict) -> None:
    assert hierarchy["network-resiliency-agent"]["dir"] == "agents/worker"


def test_leaf_tools_reference_network_resilience(hierarchy: dict) -> None:
    assert hierarchy["network-resiliency-agent"]["tools"] == [
        "network-resilience"
    ]


def test_ops_excellence_lists_leaf_as_child(hierarchy: dict) -> None:
    children = hierarchy["ops-excellence-agent"]["children"]
    assert "network-resiliency-agent" in children
    # Health-events-agent must still be a child — we're adding, not replacing.
    assert "health-events-agent" in children


def test_ops_excellence_prompt_has_pick_one_pattern(hierarchy: dict) -> None:
    """Finops-style 'pick ONE' routing — prevents orchestrator fan-out."""
    prompt = hierarchy["ops-excellence-agent"]["prompt"]
    assert "pick ONE agent per request" in prompt


def test_ops_excellence_prompt_lists_both_children(hierarchy: dict) -> None:
    prompt = hierarchy["ops-excellence-agent"]["prompt"]
    assert "health-events-agent" in prompt
    assert "network-resiliency-agent" in prompt


def test_ops_excellence_prompt_forbids_clarifying_questions(
    hierarchy: dict,
) -> None:
    """Load-bearing orchestrator prompt rule (see docs/development.md "Prompt Design Rules")."""
    prompt = hierarchy["ops-excellence-agent"]["prompt"]
    assert "NEVER ask clarifying questions" in prompt


def test_tools_json_has_network_resilience_target(tools_json: dict) -> None:
    assert "network-resilience" in tools_json


def test_tools_json_exposes_all_six_tools(tools_json: dict) -> None:
    names = {t["name"] for t in tools_json["network-resilience"]["tools"]}
    assert names == {
        "get_today_date",
        "discover_dx_topology",
        "assess_dx_resiliency",
        "get_recommendation_details",
        "get_dx_pricing",
        "estimate_upgrade_cost",
    }


def test_tools_json_handler_path(tools_json: dict) -> None:
    assert tools_json["network-resilience"]["handler"] == "handler.handler"


def test_tools_json_iam_includes_core_network_actions(tools_json: dict) -> None:
    """Spot-check that core IAM actions are present; full list is documented
    in the plan. A missing permission causes AccessDenied at runtime."""
    actions = set(tools_json["network-resilience"]["iam_actions"])
    for core in (
        "directconnect:DescribeConnections",
        "directconnect:DescribeVirtualInterfaces",
        "ec2:DescribeVpcs",
        "ec2:DescribeTransitGateways",
        "networkmanager:ListCoreNetworks",
        "cloudwatch:GetMetricData",
        "sts:GetCallerIdentity",
    ):
        assert core in actions, f"missing IAM action: {core}"
