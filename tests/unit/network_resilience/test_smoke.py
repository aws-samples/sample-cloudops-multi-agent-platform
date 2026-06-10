"""Phase 1 smoke tests for the network-resilience MCP tool.

Covers:
- Every new module imports cleanly (catches syntax/circular-import bugs the
  handler path wouldn't exercise).
- All 6 mock scenarios load and JSON round-trip without losing fidelity.
- Handler routes to the two exposed tools and handles unknown inputs.
- Handler return values are JSON-serializable (they must survive Lambda's
  JSON encoder on the way to the gateway).

Phase 2 adds assessment-rule tests here. This file stays as a regression
baseline for the topology fetch/mock path.
"""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest

_MODULES_TO_IMPORT = [
    "network_resilience",
    "network_resilience.types",
    "network_resilience.engine",
    "network_resilience.engine.sla_gating",
    "network_resilience.engine.resiliency_rules",
    "network_resilience.engine.bestpractice_rules",
    "network_resilience.engine.recommendation_engine",
    "network_resilience.engine.pricing",
    "network_resilience.topology",
    "network_resilience.topology.clients",
    "network_resilience.topology.mocks",
    "network_resilience.topology.direct_connect",
    "network_resilience.topology.ec2",
    "network_resilience.topology.cloud_wan",
    "network_resilience.topology.cloudwatch_dx",
    "network_resilience.topology.health_dx",
    "network_resilience.topology.organizations",
    "network_resilience.topology.regions",
    "network_resilience.topology.fetch",
    "handler",
]


@pytest.mark.parametrize("module_name", _MODULES_TO_IMPORT)
def test_module_imports(module_name: str) -> None:
    """Each new module must import without raising."""
    importlib.import_module(module_name)


_SCENARIO_SLUGS = (
    "noResiliency",
    "devTest",
    "high",
    "maximum",
    "crossAccount",
    "cloudWan",
)


@pytest.mark.parametrize("slug", _SCENARIO_SLUGS)
def test_mock_scenario_loads_and_roundtrips(slug: str) -> None:
    """Every fixture loads, serializes to JSON, and parses back unchanged."""
    from network_resilience.topology import mocks

    topo = mocks.load_scenario(slug)
    assert topo is not None, f"mock {slug} not found"

    # JSON round-trip — must not raise and must be structurally identical
    serialized = json.dumps(topo)
    parsed = json.loads(serialized)
    assert parsed == topo

    # Sanity: at minimum the required top-level keys are present
    for key in ("connections", "virtualInterfaces", "dxGateways", "vpcs"):
        assert key in topo, f"{slug} missing top-level key {key}"


def test_available_scenarios_matches_parametrize() -> None:
    """Guard against silently drifting scenario inventory."""
    from network_resilience.topology import mocks

    assert tuple(mocks.available_scenarios()) == _SCENARIO_SLUGS


def _ctx(tool_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        client_context=SimpleNamespace(
            custom={
                "bedrockAgentCoreToolName": f"network-resilience___{tool_name}"
            }
        )
    )


def test_handler_get_today_date() -> None:
    import handler as handler_mod

    resp = handler_mod.handler({}, _ctx("get_today_date"))
    assert resp["status"] == "success"
    # YYYY-MM-DD shape, not a specific date (avoids flaky date assertions)
    today = resp["data"]["today"]
    assert len(today) == 10 and today[4] == "-" and today[7] == "-"


@pytest.mark.parametrize("slug", _SCENARIO_SLUGS)
def test_handler_discover_mock(slug: str) -> None:
    import handler as handler_mod

    resp = handler_mod.handler(
        {"mock_scenario": slug}, _ctx("discover_dx_topology")
    )
    assert resp["status"] == "success", resp
    # Must be JSON-serializable end-to-end (Lambda will do this on return)
    json.dumps(resp)


def test_handler_unknown_mock_scenario() -> None:
    import handler as handler_mod

    resp = handler_mod.handler(
        {"mock_scenario": "does-not-exist"},
        _ctx("discover_dx_topology"),
    )
    assert resp["status"] == "error"
    assert "valid_scenarios" in resp


@pytest.mark.parametrize("slug", _SCENARIO_SLUGS)
def test_handler_assess_resiliency(slug: str) -> None:
    """assess_dx_resiliency runs end-to-end against every mock scenario."""
    import handler as handler_mod

    resp = handler_mod.handler(
        {"mock_scenario": slug}, _ctx("assess_dx_resiliency")
    )
    assert resp["status"] == "success", resp
    data = resp["data"]
    # Paired response: topology + assessment in one tool result so the
    # visualizer renders nodes/edges and recommendations from a single call.
    assert "topology" in data, f"{slug}: missing topology"
    assert "assessment" in data, f"{slug}: missing assessment"
    assessment = data["assessment"]
    for k in ("perDxGateway", "global", "resiliency", "bestPractice"):
        assert k in assessment, f"{slug}: missing {k}"
    # Must survive JSON encode
    json.dumps(resp)


def test_handler_assess_with_passed_topology() -> None:
    """When caller passes a topology, handler skips discovery."""
    import handler as handler_mod
    from network_resilience.topology import mocks

    topo = mocks.load_scenario("high")
    resp = handler_mod.handler(
        {"topology": topo, "targets": "maximum"},
        _ctx("assess_dx_resiliency"),
    )
    assert resp["status"] == "success"
    assert resp["data"]["assessment"]["resiliency"]["targetLevel"] == "maximum"


def test_handler_get_recommendation_details_missing_id() -> None:
    import handler as handler_mod

    resp = handler_mod.handler({}, _ctx("get_recommendation_details"))
    assert resp["status"] == "error"
    assert "recommendation_id" in resp["error"]


def test_handler_get_recommendation_details_not_found() -> None:
    import handler as handler_mod

    resp = handler_mod.handler(
        {
            "recommendation_id": "does-not-exist",
            "mock_scenario": "maximum",
        },
        _ctx("get_recommendation_details"),
    )
    assert resp["status"] == "error"


def test_handler_get_recommendation_details_found() -> None:
    """bfd-guidance fires on any non-empty topology — use that as a stable ID."""
    import handler as handler_mod

    resp = handler_mod.handler(
        {
            "recommendation_id": "bp-bfd-guidance",
            "mock_scenario": "maximum",
        },
        _ctx("get_recommendation_details"),
    )
    assert resp["status"] == "success", resp
    assert resp["data"]["ruleId"] == "bfd-guidance"


def test_handler_get_dx_pricing_validates_required() -> None:
    import handler as handler_mod

    resp = handler_mod.handler({}, _ctx("get_dx_pricing"))
    assert resp["status"] == "error"
    assert "region" in resp["error"] and "port_speed" in resp["error"]


def test_handler_get_dx_pricing_validates_speed() -> None:
    import handler as handler_mod

    resp = handler_mod.handler(
        {"region": "us-east-1", "port_speed": "999Gbps"},
        _ctx("get_dx_pricing"),
    )
    assert resp["status"] == "error"


def test_handler_estimate_upgrade_cost_validates_tier() -> None:
    import handler as handler_mod

    resp = handler_mod.handler(
        {"target_tier": "ludicrous", "mock_scenario": "maximum"},
        _ctx("estimate_upgrade_cost"),
    )
    assert resp["status"] == "error"
    assert "target_tier" in resp["error"]


def test_handler_unknown_tool() -> None:
    import handler as handler_mod

    resp = handler_mod.handler({}, _ctx("nonexistent_tool"))
    assert "error" in resp
    assert resp["error"].startswith("Unknown tool")
    # The handler's tool set grows phase-by-phase; this test just guards the
    # error-envelope contract, not a specific inventory. Inventory coverage
    # lives in ``test_handler_tool_inventory``.
    assert isinstance(resp.get("available_tools"), list)
    assert len(resp["available_tools"]) > 0


def test_handler_tool_inventory() -> None:
    """Guard against silently dropping or renaming a tool. Update this list
    intentionally when adding or removing a tool handler.
    """
    import handler as handler_mod

    resp = handler_mod.handler({}, _ctx("nonexistent_tool"))
    assert set(resp["available_tools"]) == {
        "get_today_date",
        "discover_dx_topology",
        "assess_dx_resiliency",
        "get_recommendation_details",
        "get_dx_pricing",
        "estimate_upgrade_cost",
    }
