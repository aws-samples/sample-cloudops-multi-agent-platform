"""Pytest coverage for the ``network-resilience-api`` REST Lambda
(``src/lambda/frontend/network-resilience/handler.py``).

Covers routing, request validation, and the critical invariant that
``/reassess`` produces byte-identical output to the agent-flow
``assess_dx_resiliency`` tool (both paths import the same shared
``network_resilience.engine`` package).
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_API_LAMBDA_DIR = _REPO / "src" / "lambda" / "frontend" / "network-resilience"


@pytest.fixture(scope="module")
def api_handler():
    """Import the api Lambda's handler with a clean module slot.

    The MCP Lambda also has a ``handler`` module; sys.modules caching
    means we need to evict it to avoid pulling the wrong one.
    """
    sys.modules.pop("handler", None)
    if str(_API_LAMBDA_DIR) not in sys.path:
        sys.path.insert(0, str(_API_LAMBDA_DIR))
    mod = importlib.import_module("handler")
    yield mod
    sys.modules.pop("handler", None)
    if str(_API_LAMBDA_DIR) in sys.path:
        sys.path.remove(str(_API_LAMBDA_DIR))


def _event(method: str, path: str, body=None) -> dict:
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "rawPath": path,
        "body": json.dumps(body) if body is not None else None,
    }


def _empty_topology() -> dict:
    return {
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


def test_health(api_handler) -> None:
    r = api_handler.handler(_event("GET", "/network-resilience/health"), None)
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert body["status"] == "ok"
    assert "version" in body


def test_reassess_empty_topology(api_handler) -> None:
    r = api_handler.handler(
        _event(
            "POST",
            "/network-resilience/reassess",
            {"topology": _empty_topology()},
        ),
        None,
    )
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert body["assessment"]["resiliency"]["currentLevel"] == "none"
    assert body["assessment"]["resiliency"]["score"] == 0


def test_reassess_single_conn_devtest(api_handler) -> None:
    t = _empty_topology()
    t["connections"] = [
        {
            "connectionId": "c1",
            "connectionName": "c1",
            "location": "EqDC2",
            "connectionState": "available",
        }
    ]
    r = api_handler.handler(
        _event("POST", "/network-resilience/reassess", {"topology": t}),
        None,
    )
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert body["assessment"]["resiliency"]["currentLevel"] == "devtest"
    assert body["assessment"]["resiliency"]["score"] == 30


def test_reassess_scalar_target(api_handler) -> None:
    t = _empty_topology()
    t["connections"] = [
        {"connectionId": "c1", "location": "EqDC2", "connectionState": "available"}
    ]
    r = api_handler.handler(
        _event(
            "POST",
            "/network-resilience/reassess",
            {"topology": t, "targetTiers": "maximum"},
        ),
        None,
    )
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert body["assessment"]["resiliency"]["targetLevel"] == "maximum"


def test_reassess_per_dxgw_target_map(api_handler) -> None:
    t = _empty_topology()
    t["dxGateways"] = [
        {
            "directConnectGatewayId": "gw-1",
            "directConnectGatewayName": "gw-1",
            "amazonSideAsn": 64512,
            "directConnectGatewayState": "available",
        }
    ]
    t["connections"] = [
        {"connectionId": "c1", "location": "EqDC2", "connectionState": "available"}
    ]
    t["virtualInterfaces"] = [
        {
            "virtualInterfaceId": "v1",
            "connectionId": "c1",
            "location": "EqDC2",
            "directConnectGatewayId": "gw-1",
            "virtualInterfaceState": "available",
            "bgpPeers": [{"bgpStatus": "up"}],
        }
    ]
    r = api_handler.handler(
        _event(
            "POST",
            "/network-resilience/reassess",
            {"topology": t, "targetTiers": {"gw-1": "maximum"}},
        ),
        None,
    )
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert len(body["assessment"]["perDxGateway"]) == 1
    assert body["assessment"]["perDxGateway"][0]["targetLevel"] == "maximum"


def test_reassess_missing_topology(api_handler) -> None:
    r = api_handler.handler(
        _event("POST", "/network-resilience/reassess", {}), None
    )
    assert r["statusCode"] == 400
    assert "topology" in json.loads(r["body"])["error"]


def test_reassess_invalid_scalar_target(api_handler) -> None:
    r = api_handler.handler(
        _event(
            "POST",
            "/network-resilience/reassess",
            {"topology": _empty_topology(), "targetTiers": "ludicrous"},
        ),
        None,
    )
    assert r["statusCode"] == 400


def test_reassess_invalid_target_in_map(api_handler) -> None:
    r = api_handler.handler(
        _event(
            "POST",
            "/network-resilience/reassess",
            {"topology": _empty_topology(), "targetTiers": {"gw-1": "low"}},
        ),
        None,
    )
    assert r["statusCode"] == 400


def test_reassess_malformed_json(api_handler) -> None:
    bad = {
        "requestContext": {
            "http": {"method": "POST", "path": "/network-resilience/reassess"}
        },
        "rawPath": "/network-resilience/reassess",
        "body": "{not-json",
    }
    r = api_handler.handler(bad, None)
    assert r["statusCode"] == 400


def test_reassess_byte_identical_to_agent_flow(api_handler) -> None:
    """Critical invariant: the REST fast-path and the agent-flow tool
    produce identical assessments for the same inputs. Both import the
    same shared ``network_resilience.engine.recommendation_engine``.

    If this ever fails, either the engine was forked or the Lambda zips
    stopped shipping the same version.
    """
    from network_resilience.engine.recommendation_engine import analyze_topology

    t = _empty_topology()
    t["connections"] = [
        {
            "connectionId": "c1",
            "connectionName": "c1",
            "location": "EqDC2",
            "connectionState": "available",
        },
        {
            "connectionId": "c2",
            "connectionName": "c2",
            "location": "EqDC6",
            "connectionState": "available",
        },
    ]
    direct = analyze_topology(t, "maximum")
    r = api_handler.handler(
        _event(
            "POST",
            "/network-resilience/reassess",
            {"topology": t, "targetTiers": "maximum"},
        ),
        None,
    )
    from_api = json.loads(r["body"])["assessment"]
    assert direct == from_api


def test_live_status_requires_vif_ids(api_handler) -> None:
    r = api_handler.handler(
        _event("POST", "/network-resilience/live-status", {}), None
    )
    assert r["statusCode"] == 400
    assert "vifIds" in json.loads(r["body"])["error"]


def test_live_status_rejects_non_string_vif_ids(api_handler) -> None:
    r = api_handler.handler(
        _event(
            "POST",
            "/network-resilience/live-status",
            {"vifIds": ["v1", 42]},
        ),
        None,
    )
    assert r["statusCode"] == 400


def test_cross_account_enrich_requires_role_arns(api_handler) -> None:
    r = api_handler.handler(
        _event("POST", "/network-resilience/cross-account-enrich", {}), None
    )
    assert r["statusCode"] == 400


def test_cross_account_enrich_stub(api_handler) -> None:
    r = api_handler.handler(
        _event(
            "POST",
            "/network-resilience/cross-account-enrich",
            {"roleArns": []},
        ),
        None,
    )
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert body["additionalVpcs"] == []
    assert "Phase 7" in body["note"]


def test_unknown_route(api_handler) -> None:
    r = api_handler.handler(_event("GET", "/nope"), None)
    assert r["statusCode"] == 404


def test_utilization_requires_ids(api_handler) -> None:
    r = api_handler.handler(
        _event(
            "POST",
            "/network-resilience/utilization",
            {"windowDays": 30},
        ),
        None,
    )
    assert r["statusCode"] == 400


def test_utilization_rejects_invalid_window(api_handler) -> None:
    r = api_handler.handler(
        _event(
            "POST",
            "/network-resilience/utilization",
            {"vifIds": ["v1"], "windowDays": 7},
        ),
        None,
    )
    assert r["statusCode"] == 400
    assert "windowDays" in json.loads(r["body"])["error"]


def test_utilization_rejects_non_string_ids(api_handler) -> None:
    r = api_handler.handler(
        _event(
            "POST",
            "/network-resilience/utilization",
            {"vifIds": ["v1", 42], "windowDays": 30},
        ),
        None,
    )
    assert r["statusCode"] == 400


def test_utilization_calls_shared_fetcher(api_handler, monkeypatch) -> None:
    """The route must delegate to the same network_resilience.topology
    fetcher the agent path uses, so VIF + connection peak math stays in
    sync between the two callers."""
    captured: dict = {}

    def fake_fetch(vifs, conns, region, window_days):
        captured["vif_ids"] = [v["virtualInterfaceId"] for v in vifs]
        captured["conn_ids"] = [c["connectionId"] for c in conns]
        captured["region"] = region
        captured["window_days"] = window_days
        return {
            "vif": {"v1": {"ingressBpsPeak": 100, "egressBpsPeak": 50}},
            "connection": {"c1": {"ingressBpsPeak": 200}},
        }

    monkeypatch.setattr(
        "network_resilience.topology.cloudwatch_dx.fetch_utilization",
        fake_fetch,
    )

    r = api_handler.handler(
        _event(
            "POST",
            "/network-resilience/utilization",
            {
                "vifIds": ["v1"],
                "connectionIds": ["c1"],
                "region": "ap-southeast-1",
                "windowDays": 60,
            },
        ),
        None,
    )
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert body["region"] == "ap-southeast-1"
    assert body["windowDays"] == 60
    assert body["vif"]["v1"]["ingressBpsPeak"] == 100
    assert body["connection"]["c1"]["ingressBpsPeak"] == 200
    assert captured == {
        "vif_ids": ["v1"],
        "conn_ids": ["c1"],
        "region": "ap-southeast-1",
        "window_days": 60,
    }
