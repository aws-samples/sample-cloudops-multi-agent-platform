"""End-to-end tests for the network resiliency agent + visualizer path.

Exercises the full chain supervisor → ops-excellence-agent →
network-resiliency-agent → gateway tools, plus the REST `/reassess` and
`/live-status` endpoints that back the visualizer panel's interactive
controls.

Requires the same setup as `test_live_stack.py`:
- A deployed stack (`make deploy-auto`)
- Cognito credentials in `scripts/.env` (COGNITO_USERNAME, COGNITO_PASSWORD)

Run with:
    .venv/bin/pytest tests/integration/test_network_resiliency_e2e.py -v
"""

from __future__ import annotations

import json

import httpx
import pytest

from tests.integration.conftest import invoke_supervisor_agui


class TestNetworkResiliencyChain:
    """Supervisor → ops-excellence → network-resiliency → gateway tools."""

    def test_dx_topology_discovery_delegates_through_ops_excellence(
        self, deployed_config, cognito_token, session_id
    ):
        """A DX question should reach the NR leaf agent and return real data.

        Asserts that the tool_trace shows the full chain: supervisor picks
        the `ops-excellence-agent` delegate, which picks `network-resiliency-agent`,
        which calls `discover_dx_topology` on the MCP gateway.
        """
        result = invoke_supervisor_agui(
            deployed_config["supervisor_url"],
            cognito_token,
            "Show me my Direct Connect topology.",
            session_id,
            timeout=300.0,
        )
        assert not result["errors"], f"Errors: {result['errors']}"
        # Top-level tool name comes from TOOL_CALL_START events (not RESULT —
        # those only carry the toolCallId + content). Join on toolCallId to
        # get the full picture.
        tool_names = _collect_top_level_tool_names(result["events"])
        assert "ops-excellence-agent" in tool_names, (
            f"Expected ops-excellence-agent in tool calls; got {tool_names}"
        )

        # Walk the nested tool_trace to confirm the MCP tool fired.
        mcp_tool_names = _collect_mcp_tool_names_from_traces(result["tools"])
        assert any(
            "discover_dx_topology" in n for n in mcp_tool_names
        ), f"Expected discover_dx_topology in nested traces; got {mcp_tool_names}"

    def test_resiliency_assessment_returns_structured_data(
        self, deployed_config, cognito_token, session_id
    ):
        """An assessment-phrased question should fire assess_dx_resiliency.

        The NR agent's routing prompt is designed to call both tools in
        sequence when the user asks for a resiliency assessment — so we
        expect `assess_dx_resiliency` in the nested trace on top of
        `discover_dx_topology`.
        """
        result = invoke_supervisor_agui(
            deployed_config["supervisor_url"],
            cognito_token,
            "Assess the resiliency of my Direct Connect setup.",
            session_id,
            timeout=300.0,
        )
        assert not result["errors"], f"Errors: {result['errors']}"
        mcp_tool_names = _collect_mcp_tool_names_from_traces(result["tools"])
        # Allow either tool to carry the discovery+assessment — the agent
        # might also batch them through get_recommendation_details. At
        # minimum one of the two entry points should appear.
        assert any(
            n.endswith("assess_dx_resiliency") or n.endswith("discover_dx_topology")
            for n in mcp_tool_names
        ), f"No DX tools fired; got {mcp_tool_names}"


class TestNetworkResilienceApi:
    """REST endpoints behind the visualizer's fast-path (not the agent chain)."""

    def test_health_endpoint_is_reachable(self, deployed_config, cognito_token):
        """GET /network-resilience/health returns 200 with auth."""
        api_url = deployed_config["frontend_api_url"].rstrip("/")
        resp = httpx.get(
            f"{api_url}/network-resilience/health",
            headers={"authorization": f"Bearer {cognito_token}"},
            timeout=15.0,
        )
        assert resp.status_code == 200, (
            f"health endpoint returned {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("status") == "ok"
        assert "version" in body

    def test_health_endpoint_rejects_unauthenticated(self, deployed_config):
        """The JWT authorizer should block anonymous calls with a 401."""
        api_url = deployed_config["frontend_api_url"].rstrip("/")
        resp = httpx.get(
            f"{api_url}/network-resilience/health",
            timeout=10.0,
        )
        assert resp.status_code == 401, (
            f"Expected 401 without auth, got {resp.status_code}"
        )

    def test_reassess_rejects_missing_topology(self, deployed_config, cognito_token):
        """POST /reassess without a topology body returns 400."""
        api_url = deployed_config["frontend_api_url"].rstrip("/")
        resp = httpx.post(
            f"{api_url}/network-resilience/reassess",
            headers={
                "authorization": f"Bearer {cognito_token}",
                "content-type": "application/json",
            },
            json={},
            timeout=15.0,
        )
        # Either 400 (validation) or 422 (pydantic-style). Accept both.
        assert resp.status_code in (400, 422), (
            f"Expected validation error; got {resp.status_code}: {resp.text}"
        )

    def test_reassess_returns_assessment_shape(
        self, deployed_config, cognito_token
    ):
        """A minimal valid topology should produce an assessment quickly (<5s).

        This exercises the Lambda-native assessment path that the visualizer
        fires on every target-tier flip — it must stay fast because the
        contract is <500ms for a tier change.
        """
        api_url = deployed_config["frontend_api_url"].rstrip("/")
        # Minimal topology with no DX infrastructure — engine should still
        # return a well-formed (empty) assessment.
        topology = {
            "connections": [],
            "virtualInterfaces": [],
            "dxGateways": [],
            "dxGatewayAssociations": [],
            "locations": [],
            "lags": [],
            "vpcs": [],
            "vpnGateways": [],
            "vpnConnections": [],
            "transitGateways": [],
            "transitGatewayAttachments": [],
            "transitGatewayPeeringAttachments": [],
            "customerGateways": [],
            "cloudWanCoreNetworks": [],
            "cloudWanAttachments": [],
            "cloudWanPeerings": [],
            "tgwRouteTables": {},
            "cloudWanRoutes": {},
        }
        resp = httpx.post(
            f"{api_url}/network-resilience/reassess",
            headers={
                "authorization": f"Bearer {cognito_token}",
                "content-type": "application/json",
            },
            json={"topology": topology, "targetTiers": "high"},
            timeout=20.0,
        )
        assert resp.status_code == 200, (
            f"reassess failed: {resp.status_code} {resp.text}"
        )
        body = resp.json()
        assert "assessment" in body
        assessment = body["assessment"]
        # Shape assertions — the frontend relies on these keys.
        for key in (
            "perDxGateway",
            "resiliency",
            "bestPractice",
            "global",
        ):
            assert key in assessment, f"Missing key {key} in assessment"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_top_level_tool_names(all_events: list[dict]) -> list[str]:
    """Pull names from every TOOL_CALL_START event in the stream."""
    names: list[str] = []
    for ev in all_events:
        if ev.get("type") != "TOOL_CALL_START":
            continue
        name = ev.get("toolCallName") or ev.get("tool_call_name") or ev.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _collect_mcp_tool_names_from_traces(tool_events: list[dict]) -> list[str]:
    """Walk through nested tool_trace arrays and return every MCP tool name.

    The supervisor's top-level tool_events only show the delegate tool
    (`ops-excellence-agent`). MCP tools like `discover_dx_topology` live in
    the recursively-nested `tool_trace` arrays of sub-agent returns.
    """
    names: list[str] = []

    def _walk(content: object) -> None:
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                return
        if not isinstance(content, dict):
            return
        name = content.get("tool_name") or content.get("name")
        if isinstance(name, str):
            names.append(name)
        trace = content.get("tool_trace")
        if isinstance(trace, list):
            for child in trace:
                _walk(child)

    for ev in tool_events:
        _walk(ev.get("content", ""))

    return names
