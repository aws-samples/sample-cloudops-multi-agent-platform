"""Unit tests for ``network_resilience.topology.cloudwatch_dx.fetch_utilization``.

The fetcher walks ListMetrics + GetMetricData against AWS/DX. We stub the
CloudWatch client so the test exercises the dimension-routing and
peak-bucket math without hitting AWS.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List

import pytest


@pytest.fixture
def stub_cw(monkeypatch):
    """Replace ``clients.cloudwatch`` with a programmable stub.

    The stub captures ``list_metrics`` / ``get_metric_data`` call args and
    returns whatever the test queues in ``stub_cw.queue_*`` lists.
    """

    class _StubClient:
        def __init__(self) -> None:
            self.list_metrics_calls: List[Dict[str, Any]] = []
            self.get_metric_data_calls: List[Dict[str, Any]] = []
            self._list_metrics_pages: List[Dict[str, Any]] = []
            self._get_metric_data_pages: List[Dict[str, Any]] = []

        def list_metrics(self, **kwargs):  # noqa: D401
            self.list_metrics_calls.append(kwargs)
            if not self._list_metrics_pages:
                return {"Metrics": [], "NextToken": None}
            return self._list_metrics_pages.pop(0)

        def get_metric_data(self, **kwargs):
            self.get_metric_data_calls.append(kwargs)
            if not self._get_metric_data_pages:
                return {"MetricDataResults": []}
            return self._get_metric_data_pages.pop(0)

    stub = _StubClient()
    from network_resilience.topology import clients as _clients_mod

    monkeypatch.setattr(_clients_mod, "cloudwatch", lambda _region: stub)
    return stub


def _ts(hour: int) -> _dt.datetime:
    return _dt.datetime(2026, 5, 1, hour, 0, 0, tzinfo=_dt.timezone.utc)


def test_fetch_utilization_returns_empty_for_no_inputs(stub_cw) -> None:
    from network_resilience.topology.cloudwatch_dx import fetch_utilization

    out = fetch_utilization([], [], "us-east-1", 30)
    assert out == {"vif": {}, "connection": {}}
    assert stub_cw.list_metrics_calls == []


def test_fetch_utilization_rejects_invalid_window() -> None:
    from network_resilience.topology.cloudwatch_dx import fetch_utilization

    with pytest.raises(ValueError):
        fetch_utilization(
            [{"virtualInterfaceId": "v1", "region": "us-east-1"}],
            [],
            "us-east-1",
            7,
        )


def test_fetch_utilization_routes_to_both_accumulators(stub_cw) -> None:
    """A single AWS/DX bps stream carries both VirtualInterfaceId and
    ConnectionId — fetching it once must populate BOTH accumulators."""
    from network_resilience.topology.cloudwatch_dx import fetch_utilization

    # ListMetrics returns one stream per direction (ingress + egress) that
    # matches both wanted v1 and wanted c1.
    stub_cw._list_metrics_pages = [
        {  # ingress ListMetrics page 1
            "Metrics": [
                {
                    "Namespace": "AWS/DX",
                    "MetricName": "VirtualInterfaceBpsIngress",
                    "Dimensions": [
                        {"Name": "VirtualInterfaceId", "Value": "v1"},
                        {"Name": "ConnectionId", "Value": "c1"},
                    ],
                }
            ],
            "NextToken": None,
        },
        {  # egress ListMetrics page 1
            "Metrics": [
                {
                    "Namespace": "AWS/DX",
                    "MetricName": "VirtualInterfaceBpsEgress",
                    "Dimensions": [
                        {"Name": "VirtualInterfaceId", "Value": "v1"},
                        {"Name": "ConnectionId", "Value": "c1"},
                    ],
                }
            ],
            "NextToken": None,
        },
    ]
    # GetMetricData: 3 hourly samples per stream, peak = 200 ingress, 150 egress.
    stub_cw._get_metric_data_pages = [
        {
            "MetricDataResults": [
                {
                    "Id": "m0",  # ingress
                    "Timestamps": [_ts(0), _ts(1), _ts(2)],
                    "Values": [100.0, 200.0, 150.0],
                },
                {
                    "Id": "m1",  # egress
                    "Timestamps": [_ts(0), _ts(1), _ts(2)],
                    "Values": [50.0, 100.0, 150.0],
                },
            ]
        }
    ]

    out = fetch_utilization(
        [{"virtualInterfaceId": "v1", "region": "us-east-1"}],
        [{"connectionId": "c1", "region": "us-east-1"}],
        "us-east-1",
        30,
    )

    assert out["vif"]["v1"] == {"ingressBpsPeak": 200, "egressBpsPeak": 150}
    assert out["connection"]["c1"] == {
        "ingressBpsPeak": 200,
        "egressBpsPeak": 150,
    }


def test_fetch_utilization_sums_sibling_vifs_per_connection(stub_cw) -> None:
    """Two VIFs on one connection: connection peak = max hour of summed
    sibling VIF traffic, not sum of per-VIF peaks."""
    from network_resilience.topology.cloudwatch_dx import fetch_utilization

    stub_cw._list_metrics_pages = [
        {
            "Metrics": [
                {
                    "Namespace": "AWS/DX",
                    "MetricName": "VirtualInterfaceBpsIngress",
                    "Dimensions": [
                        {"Name": "VirtualInterfaceId", "Value": "v1"},
                        {"Name": "ConnectionId", "Value": "c1"},
                    ],
                },
                {
                    "Namespace": "AWS/DX",
                    "MetricName": "VirtualInterfaceBpsIngress",
                    "Dimensions": [
                        {"Name": "VirtualInterfaceId", "Value": "v2"},
                        {"Name": "ConnectionId", "Value": "c1"},
                    ],
                },
            ],
            "NextToken": None,
        },
        {"Metrics": [], "NextToken": None},  # egress page
    ]
    # v1 peaks at hour 0 (300), v2 peaks at hour 1 (300). Connection peaks
    # at hour 0 with 300+100=400, NOT at v1's individual peak of 300.
    stub_cw._get_metric_data_pages = [
        {
            "MetricDataResults": [
                {
                    "Id": "m0",
                    "Timestamps": [_ts(0), _ts(1)],
                    "Values": [300.0, 200.0],
                },
                {
                    "Id": "m1",
                    "Timestamps": [_ts(0), _ts(1)],
                    "Values": [100.0, 300.0],
                },
            ]
        }
    ]

    out = fetch_utilization(
        [
            {"virtualInterfaceId": "v1", "region": "us-east-1"},
            {"virtualInterfaceId": "v2", "region": "us-east-1"},
        ],
        [{"connectionId": "c1", "region": "us-east-1"}],
        "us-east-1",
        30,
    )

    assert out["vif"]["v1"]["ingressBpsPeak"] == 300
    assert out["vif"]["v2"]["ingressBpsPeak"] == 300
    # Hour 0: 300 + 100 = 400. Hour 1: 200 + 300 = 500. Peak = 500.
    assert out["connection"]["c1"]["ingressBpsPeak"] == 500


def test_fetch_utilization_isolates_per_region_failures(stub_cw, caplog) -> None:
    """Stub the CloudWatch client to raise. A single-region failure must
    not abort the whole fetch — log warning, return what we have."""
    from network_resilience.topology.cloudwatch_dx import fetch_utilization

    def _boom(*_a, **_k):
        raise RuntimeError("ListMetrics failed")

    stub_cw.list_metrics = _boom  # type: ignore[assignment]

    out = fetch_utilization(
        [{"virtualInterfaceId": "v1", "region": "us-east-1"}],
        [],
        "us-east-1",
        30,
    )
    assert out == {"vif": {}, "connection": {}}


def test_fetch_utilization_groups_streams_by_region(stub_cw) -> None:
    """VIFs in two regions must trigger one ListMetrics call per region."""
    from network_resilience.topology.cloudwatch_dx import fetch_utilization

    fetch_utilization(
        [
            {"virtualInterfaceId": "v1", "region": "us-east-1"},
            {"virtualInterfaceId": "v2", "region": "ap-southeast-1"},
        ],
        [],
        "us-east-1",
        30,
    )
    # 2 metric names × 2 regions = 4 calls (ListMetrics is namespace+metric)
    assert len(stub_cw.list_metrics_calls) == 4
