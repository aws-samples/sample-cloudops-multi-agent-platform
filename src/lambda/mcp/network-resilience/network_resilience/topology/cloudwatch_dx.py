"""CloudWatch BGP prefix metrics + VIF/connection utilization fetcher.

Python port of source ``dx-visualizer/src/api/cloudwatch-dx.ts`` and
``cloudwatch-utilization.ts``.

AWS/DX publishes BGP prefix metrics with a VirtualInterfaceId dimension PLUS
an undocumented address-family dimension (IPv4/IPv6). We discover the actual
metric streams via ListMetrics, then sum per-VIF across address families.

The bps utilization fetcher does the same dance one level out: each
``VirtualInterfaceBpsIngress`` / ``...Egress`` stream carries BOTH a
``VirtualInterfaceId`` and ``ConnectionId`` dimension, so a single ListMetrics
+ GetMetricData per region populates both VIF and connection accumulators.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Dict, List, Set

from ..types import (
    BgpPrefixMetric,
    ConnectionUtilization,
    DxConnection,
    DxVirtualInterface,
    VifUtilization,
)
from . import clients

logger = logging.getLogger(__name__)

_METRIC_NAMES = (
    "VirtualInterfaceBgpPrefixesAccepted",
    "VirtualInterfaceBgpPrefixesAdvertised",
)


def fetch_bgp_prefix_metrics(
    vifs: List[DxVirtualInterface], default_region: str
) -> Dict[str, BgpPrefixMetric]:
    result: Dict[str, BgpPrefixMetric] = {}
    if not vifs:
        return result

    # Group VIFs by region so we can run one CloudWatch client per region.
    by_region: Dict[str, List[DxVirtualInterface]] = {}
    for vif in vifs:
        region = vif.get("region") or default_region
        by_region.setdefault(region, []).append(vif)

    now = _dt.datetime.now(tz=_dt.timezone.utc)
    start_time = now - _dt.timedelta(minutes=30)

    for region, region_vifs in by_region.items():
        try:
            _fetch_region(region, region_vifs, start_time, now, result)
        except Exception as err:  # noqa: BLE001 — per-region isolation
            logger.warning(
                "[AWS] %s/BGP prefix metrics FAILED: %s", region, err
            )

    return result


def _fetch_region(
    region: str,
    region_vifs: List[DxVirtualInterface],
    start_time: _dt.datetime,
    end_time: _dt.datetime,
    result: Dict[str, BgpPrefixMetric],
) -> None:
    cw = clients.cloudwatch(region)
    vif_ids = {v.get("virtualInterfaceId", "") for v in region_vifs}
    vif_ids.discard("")

    # Phase 1: discover which metric streams actually exist for these VIFs.
    streams: List[Dict[str, Any]] = []
    for metric_name in _METRIC_NAMES:
        next_token: str | None = None
        while True:
            kwargs: Dict[str, Any] = {
                "Namespace": "AWS/DX",
                "MetricName": metric_name,
            }
            if next_token:
                kwargs["NextToken"] = next_token
            lm = cw.list_metrics(**kwargs)
            for m in lm.get("Metrics") or []:
                vif_dim_val = _find_dim(m.get("Dimensions"), "VirtualInterfaceId")
                if vif_dim_val and vif_dim_val in vif_ids:
                    streams.append(m)
            next_token = lm.get("NextToken")
            if not next_token:
                break

    if not streams:
        logger.info(
            "[AWS] %s/BGP prefix metrics: no streams found for %d VIFs",
            region,
            len(vif_ids),
        )
        return

    # Phase 2: query every discovered stream with its exact dimensions.
    queries = []
    lookup: Dict[str, Dict[str, Any]] = {}
    for idx, m in enumerate(streams):
        qid = f"m{idx}"
        queries.append(
            {
                "Id": qid,
                "MetricStat": {
                    "Metric": {
                        "Namespace": m.get("Namespace"),
                        "MetricName": m.get("MetricName"),
                        "Dimensions": m.get("Dimensions") or [],
                    },
                    "Period": 300,
                    "Stat": "Average",
                },
                "ReturnData": True,
            }
        )
        vif_id = _find_dim(m.get("Dimensions"), "VirtualInterfaceId")
        if vif_id:
            lookup[qid] = {
                "vifId": vif_id,
                "isAccepted": m.get("MetricName")
                == "VirtualInterfaceBgpPrefixesAccepted",
            }

    batch_size = 500
    for i in range(0, len(queries), batch_size):
        batch = queries[i : i + batch_size]
        res = cw.get_metric_data(
            MetricDataQueries=batch,
            StartTime=start_time,
            EndTime=end_time,
        )
        for mdr in res.get("MetricDataResults") or []:
            qid = mdr.get("Id")
            values = mdr.get("Values") or []
            if not qid or not values:
                continue
            info = lookup.get(qid)
            if not info:
                continue
            value = int(round(values[0]))
            entry = result.get(info["vifId"]) or {}
            if info["isAccepted"]:
                entry["accepted"] = int(entry.get("accepted", 0)) + value
            else:
                entry["advertised"] = int(entry.get("advertised", 0)) + value
            result[info["vifId"]] = entry

    logger.info(
        "[AWS] %s/BGP prefix metrics: %d streams → %d VIFs with data",
        region,
        len(streams),
        len(result),
    )


def _find_dim(dims: List[Dict[str, str]] | None, name: str) -> str | None:
    for d in dims or []:
        if d.get("Name") == name:
            return d.get("Value")
    return None


# ----- Utilization (peak bps over 30/60/90 days) ----------------------------

_UTIL_METRIC_NAMES = (
    "VirtualInterfaceBpsIngress",
    "VirtualInterfaceBpsEgress",
)
# 1-hour buckets. CloudWatch retains 1-hour datapoints for ~15 months, which
# covers all supported windows. Granularity is intentionally coarse — the
# capacity-planning view, not troubleshooting.
_UTIL_PERIOD_SECONDS = 3600
_UTIL_BATCH_SIZE = 500


def fetch_utilization(
    vifs: List[DxVirtualInterface],
    connections: List[DxConnection],
    default_region: str,
    window_days: int,
) -> Dict[str, Dict[str, Any]]:
    """Fetch peak hourly bitrate per VIF and per DX Connection.

    Returns ``{"vif": {vifId: VifUtilization}, "connection": {connId: ConnectionUtilization}}``.
    Per-region failures are isolated and logged — partial results are still
    returned.

    Implementation note: AWS does not publish a ConnectionBps* metric, but
    each VIF bps stream carries both ``VirtualInterfaceId`` and
    ``ConnectionId`` dimensions. We issue ONE ListMetrics + GetMetricData
    per region and route each datapoint into both:

      - per-VIF buckets keyed by VirtualInterfaceId
      - per-connection buckets keyed by ConnectionId

    For each (key, direction, hour) bucket we sum across streams (e.g.
    address-family splits for VIFs, sibling VIFs for connections); the
    reported peak is the max bucket — i.e. the worst hour observed in the
    window.
    """
    vif_result: Dict[str, VifUtilization] = {}
    conn_result: Dict[str, ConnectionUtilization] = {}
    if not vifs and not connections:
        return {"vif": vif_result, "connection": conn_result}
    if window_days not in (30, 60, 90):
        raise ValueError(
            f"window_days must be 30, 60, or 90 (got {window_days})"
        )

    vif_ids_by_region: Dict[str, Set[str]] = {}
    conn_ids_by_region: Dict[str, Set[str]] = {}
    regions: Set[str] = set()
    for v in vifs:
        r = v.get("region") or default_region
        regions.add(r)
        vid = v.get("virtualInterfaceId")
        if vid:
            vif_ids_by_region.setdefault(r, set()).add(vid)
    for c in connections:
        r = c.get("region") or default_region
        regions.add(r)
        cid = c.get("connectionId")
        if cid:
            conn_ids_by_region.setdefault(r, set()).add(cid)

    now = _dt.datetime.now(tz=_dt.timezone.utc)
    start_time = now - _dt.timedelta(days=window_days)

    for region in regions:
        try:
            _fetch_utilization_region(
                region,
                vif_ids_by_region.get(region, set()),
                conn_ids_by_region.get(region, set()),
                start_time,
                now,
                window_days,
                vif_result,
                conn_result,
            )
        except Exception as err:  # noqa: BLE001 — per-region isolation
            logger.warning(
                "[AWS] %s/Utilization FAILED: %s", region, err
            )

    return {"vif": vif_result, "connection": conn_result}


def _fetch_utilization_region(
    region: str,
    wanted_vifs: Set[str],
    wanted_conns: Set[str],
    start_time: _dt.datetime,
    end_time: _dt.datetime,
    window_days: int,
    vif_result: Dict[str, VifUtilization],
    conn_result: Dict[str, ConnectionUtilization],
) -> None:
    cw = clients.cloudwatch(region)

    # Phase 1: discover metric streams matching either dimension. A single
    # stream may match both (the typical case for owned VIFs on owned
    # connections); fingerprint by (metric, dimensions) so we don't issue the
    # same query twice.
    streams: List[Dict[str, Any]] = []
    seen_stream_keys: Set[str] = set()
    for metric_name in _UTIL_METRIC_NAMES:
        next_token: str | None = None
        while True:
            kwargs: Dict[str, Any] = {
                "Namespace": "AWS/DX",
                "MetricName": metric_name,
            }
            if next_token:
                kwargs["NextToken"] = next_token
            lm = cw.list_metrics(**kwargs)
            for m in lm.get("Metrics") or []:
                vif_id = _find_dim(m.get("Dimensions"), "VirtualInterfaceId")
                conn_id = _find_dim(m.get("Dimensions"), "ConnectionId")
                matches_vif = bool(vif_id) and vif_id in wanted_vifs
                matches_conn = bool(conn_id) and conn_id in wanted_conns
                if not matches_vif and not matches_conn:
                    continue
                dims_key = "|".join(
                    sorted(
                        f"{d.get('Name')}={d.get('Value')}"
                        for d in m.get("Dimensions") or []
                    )
                )
                key = f"{m.get('MetricName')}::{dims_key}"
                if key in seen_stream_keys:
                    continue
                seen_stream_keys.add(key)
                streams.append(m)
            next_token = lm.get("NextToken")
            if not next_token:
                break

    if not streams:
        logger.info(
            "[AWS] %s/Utilization: no streams found (VIFs=%d, conns=%d)",
            region,
            len(wanted_vifs),
            len(wanted_conns),
        )
        return

    queries = []
    # Each stream contributes to up to two accumulators: one keyed by VIF
    # (if VirtualInterfaceId matches a wanted VIF) and one keyed by
    # connection (if ConnectionId matches a wanted connection).
    lookup: Dict[str, Dict[str, Any]] = {}
    for idx, m in enumerate(streams):
        qid = f"m{idx}"
        queries.append(
            {
                "Id": qid,
                "MetricStat": {
                    "Metric": {
                        "Namespace": m.get("Namespace"),
                        "MetricName": m.get("MetricName"),
                        "Dimensions": m.get("Dimensions") or [],
                    },
                    "Period": _UTIL_PERIOD_SECONDS,
                    "Stat": "Average",
                },
                "ReturnData": True,
            }
        )
        vif_id = _find_dim(m.get("Dimensions"), "VirtualInterfaceId")
        conn_id = _find_dim(m.get("Dimensions"), "ConnectionId")
        lookup[qid] = {
            "vifId": vif_id if (vif_id and vif_id in wanted_vifs) else None,
            "connId": (
                conn_id if (conn_id and conn_id in wanted_conns) else None
            ),
            "direction": (
                "ingress"
                if m.get("MetricName") == "VirtualInterfaceBpsIngress"
                else "egress"
            ),
        }

    # (vifId or connId) -> {ingressByBucket: {ts: bps}, egressByBucket: {...}}
    vif_accum: Dict[str, Dict[str, Dict[float, float]]] = {}
    conn_accum: Dict[str, Dict[str, Dict[float, float]]] = {}

    def _ensure(
        accum: Dict[str, Dict[str, Dict[float, float]]], key: str
    ) -> Dict[str, Dict[float, float]]:
        slot = accum.get(key)
        if slot is None:
            slot = {"ingressByBucket": {}, "egressByBucket": {}}
            accum[key] = slot
        return slot

    for i in range(0, len(queries), _UTIL_BATCH_SIZE):
        batch = queries[i : i + _UTIL_BATCH_SIZE]
        res = cw.get_metric_data(
            MetricDataQueries=batch,
            StartTime=start_time,
            EndTime=end_time,
        )
        for mdr in res.get("MetricDataResults") or []:
            qid = mdr.get("Id")
            timestamps = mdr.get("Timestamps") or []
            values = mdr.get("Values") or []
            if not qid or not timestamps or not values:
                continue
            info = lookup.get(qid)
            if not info:
                continue
            direction = info["direction"]
            target_key = (
                "ingressByBucket" if direction == "ingress" else "egressByBucket"
            )
            for ts, value in zip(timestamps, values):
                if ts is None or value is None:
                    continue
                # boto3 returns ``datetime`` for Timestamps; convert to a
                # stable numeric bucket so dict keys collide across streams.
                bucket = (
                    ts.timestamp() if hasattr(ts, "timestamp") else float(ts)
                )
                if info["vifId"]:
                    slot = _ensure(vif_accum, info["vifId"])
                    slot[target_key][bucket] = (
                        slot[target_key].get(bucket, 0.0) + float(value)
                    )
                if info["connId"]:
                    slot = _ensure(conn_accum, info["connId"])
                    slot[target_key][bucket] = (
                        slot[target_key].get(bucket, 0.0) + float(value)
                    )

    def _peak(buckets: Dict[float, float]) -> int | None:
        if not buckets:
            return None
        return int(round(max(buckets.values())))

    for vid, slot in vif_accum.items():
        ingress = _peak(slot["ingressByBucket"])
        egress = _peak(slot["egressByBucket"])
        if ingress is None and egress is None:
            continue
        entry: VifUtilization = {}
        if ingress is not None:
            entry["ingressBpsPeak"] = ingress
        if egress is not None:
            entry["egressBpsPeak"] = egress
        vif_result[vid] = entry

    for cid, slot in conn_accum.items():
        ingress = _peak(slot["ingressByBucket"])
        egress = _peak(slot["egressByBucket"])
        if ingress is None and egress is None:
            continue
        entry_c: ConnectionUtilization = {}
        if ingress is not None:
            entry_c["ingressBpsPeak"] = ingress
        if egress is not None:
            entry_c["egressBpsPeak"] = egress
        conn_result[cid] = entry_c

    logger.info(
        "[AWS] %s/Utilization (%dd peak): %d streams → %d VIFs, %d connections",
        region,
        window_days,
        len(streams),
        sum(1 for v in vif_accum if v in vif_result),
        sum(1 for c in conn_accum if c in conn_result),
    )
