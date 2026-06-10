"""Five-phase topology fetch orchestrator.

Python port of source ``dx-visualizer/src/api/fetch-topology.ts``.

Phases (preserved from source):
    1. Global services in parallel (DX Gateways, Cloud WAN, Locations).
    2. DX Gateway associations + Cloud WAN routes + default-region in parallel.
    3. Discover additional regions (from DXGW assocs + Cloud WAN edges) and
       fan out per-region fetchers.
    4. Merge per-region results (dedup by resource ID). Infer stub connections
       from orphan hosted VIFs.
    4.5. CloudWatch BGP prefix metrics + AWS Health maintenance events.
    5. (Optional) Spoke account enrichment via AssumeRole.

The ``_parallel_logged`` helper mirrors the source's ``Promise.all([logged(...)])``:
sub-call failures land in ``fetch_errors`` and return empty defaults so the
rest of the topology keeps flowing.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import re
import traceback
from typing import Any, Callable, Dict, List, Optional, TypeVar

from ..types import DxConnection, TopologyData
from . import (
    clients,
    cloud_wan,
    cloudwatch_dx,
    direct_connect,
    ec2,
    health_dx,
    mocks,
    organizations,
    regions as regions_api,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Signals "auth broke everything" vs a partial failure.
_AUTH_RE = re.compile(
    r"credential|unauthorized|InvalidIdentityToken|ExpiredToken|"
    r"SignatureDoesNotMatch|AccessDenied",
    re.IGNORECASE,
)


# ----- Public entry point ---------------------------------------------------


def fetch_all_topology_data(
    default_region: Optional[str] = None,
    mock_scenario: Optional[str] = None,
    spoke_accounts: Optional[List[str]] = None,
    cross_account_role_name: str = "NetworkReadOnlyRole",
) -> TopologyData:
    """Fetch full DX topology across all reachable regions.

    Args:
        default_region: Home region to seed discovery from. Defaults to the
            ``AWS_REGION`` env var, then us-east-1.
        mock_scenario: If set, bypass AWS entirely and return the matching
            fixture from ``mocks/``. One of: noResiliency, devTest, high,
            maximum, crossAccount, cloudWan.
        spoke_accounts: Optional list of account IDs for Phase 5 enrichment.
        cross_account_role_name: IAM role to assume in each spoke.

    Returns:
        A TopologyData dict. Partial failures land in ``fetchErrors``.
    """
    if mock_scenario:
        topo = mocks.load_scenario(mock_scenario)
        if topo is None:
            return {
                "fetchErrors": [
                    f"unknown mock_scenario: {mock_scenario}. "
                    f"valid: {', '.join(mocks.available_scenarios())}"
                ]
            }
        # Stamp the scenario name so downstream consumers (frontend
        # VisualizerCard, report generation) can caveat that these numbers
        # come from demo data rather than the user's live environment.
        return {**topo, "mockScenario": mock_scenario}  # type: ignore[return-value]

    region = default_region or os.environ.get("AWS_REGION") or "us-east-1"
    errors: List[str] = []

    # ------- Phase 1: Global services in parallel ---------------------------
    dx_global = clients.dx(region)
    nm = clients.networkmanager()

    p1_tasks: Dict[str, Callable[[], Any]] = {
        "DxGateways": lambda: direct_connect.fetch_dx_gateways(dx_global),
        "CloudWanCoreNetworks": lambda: cloud_wan.fetch_core_networks(nm),
        "CloudWanAttachments": lambda: cloud_wan.fetch_cloud_wan_attachments(nm),
        "CloudWanPeerings": lambda: cloud_wan.fetch_cloud_wan_peerings(nm),
    }
    p1_results = _parallel_logged(p1_tasks, errors, default=[])
    dx_gateways = p1_results["DxGateways"]
    cloud_wan_core_networks = p1_results["CloudWanCoreNetworks"]
    cloud_wan_attachments = p1_results["CloudWanAttachments"]
    cloud_wan_peerings = p1_results["CloudWanPeerings"]

    # ------- Phase 2: DXGW assocs + CloudWAN routes + default region --------
    p2_tasks: Dict[str, Callable[[], Any]] = {
        "DxGwAssocsAll": lambda: _fetch_all_dxgw_assocs(
            dx_global, dx_gateways, errors
        ),
        "CloudWanRoutes": (
            (lambda: cloud_wan.fetch_cloud_wan_routes(nm, cloud_wan_core_networks))
            if cloud_wan_core_networks
            else (lambda: {})
        ),
        "DefaultRegion": lambda: _fetch_region(region, errors),
    }
    p2_results = _parallel_logged(
        p2_tasks,
        errors,
        defaults={
            "DxGwAssocsAll": [],
            "CloudWanRoutes": {},
            "DefaultRegion": _empty_region_result(region),
        },
    )
    dx_gateway_associations = p2_results["DxGwAssocsAll"]
    cloud_wan_routes = p2_results["CloudWanRoutes"]
    default_region_result = p2_results["DefaultRegion"]

    # ------- Phase 3: Discover additional regions + fan out -----------------
    discovered: set[str] = set()
    for assoc in dx_gateway_associations:
        ag_region = (assoc.get("associatedGateway") or {}).get("region")
        if ag_region:
            discovered.add(ag_region)
    for cn in cloud_wan_core_networks:
        for edge in cn.get("edges") or []:
            if edge.get("edgeLocation"):
                discovered.add(edge["edgeLocation"])
    for att in cloud_wan_attachments:
        if att.get("edgeLocation"):
            discovered.add(att["edgeLocation"])
    discovered.discard(region)

    logger.info(
        "[AWS] Discovered regions: %s (pre-fetched)%s",
        region,
        f", {', '.join(sorted(discovered))}" if discovered else "",
    )

    # Region names SSM fetch in parallel with extra-region fetches.
    region_codes_for_names = [region, *sorted(discovered)]

    extra_region_results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(8, len(discovered) + 1))
    ) as pool:
        region_names_fut = pool.submit(
            regions_api.fetch_region_names, region_codes_for_names
        )
        region_futures = {
            pool.submit(_fetch_region, r, errors): r for r in discovered
        }
        for fut in concurrent.futures.as_completed(region_futures):
            r = region_futures[fut]
            try:
                extra_region_results.append(fut.result())
            except Exception as err:  # noqa: BLE001
                errors.append(f"region:{r}: {err}")
                extra_region_results.append(_empty_region_result(r))
        try:
            region_names = region_names_fut.result()
        except Exception as err:  # noqa: BLE001
            errors.append(f"SSM region-names: {err}")
            region_names = {}

    region_results = [default_region_result, *extra_region_results]

    # ------- Phase 4: Merge + hosted-VIF inference --------------------------
    seen: Dict[str, set[str]] = {
        k: set()
        for k in (
            "conn",
            "vif",
            "lag",
            "loc",
            "vpc",
            "vpngw",
            "tgw",
            "tgwatt",
            "tgwpeer",
            "vpcpeer",
            "vpnconn",
            "cgw",
        )
    }
    connections: List[DxConnection] = []
    virtual_interfaces: list = []
    lags: list = []
    locations: list = []
    vpcs: list = []
    vpn_gateways: list = []
    transit_gateways: list = []
    transit_gateway_attachments: list = []
    transit_gateway_peering_attachments: list = []
    vpc_peerings: list = []
    vpn_connections: list = []
    customer_gateways: list = []
    tgw_route_tables: Dict[str, list] = {}
    vpc_route_tables: Dict[str, list] = {}

    for r in region_results:
        for c in r.get("connections") or []:
            if c.get("connectionId") not in seen["conn"]:
                connections.append(c)
                seen["conn"].add(c.get("connectionId"))
        for v in r.get("virtualInterfaces") or []:
            if v.get("virtualInterfaceId") not in seen["vif"]:
                virtual_interfaces.append(v)
                seen["vif"].add(v.get("virtualInterfaceId"))
        for l in r.get("lags") or []:
            if l.get("lagId") not in seen["lag"]:
                lags.append(l)
                seen["lag"].add(l.get("lagId"))
        for loc in r.get("locations") or []:
            code = loc.get("locationCode")
            if code and code not in seen["loc"]:
                locations.append(loc)
                seen["loc"].add(code)
        for v in r.get("vpcs") or []:
            if v.get("vpcId") not in seen["vpc"]:
                vpcs.append(v)
                seen["vpc"].add(v.get("vpcId"))
        for g in r.get("vpnGateways") or []:
            if g.get("vpnGatewayId") not in seen["vpngw"]:
                vpn_gateways.append(g)
                seen["vpngw"].add(g.get("vpnGatewayId"))
        for t in r.get("transitGateways") or []:
            if t.get("transitGatewayId") not in seen["tgw"]:
                transit_gateways.append(t)
                seen["tgw"].add(t.get("transitGatewayId"))
        for a in r.get("transitGatewayAttachments") or []:
            if a.get("transitGatewayAttachmentId") not in seen["tgwatt"]:
                transit_gateway_attachments.append(a)
                seen["tgwatt"].add(a.get("transitGatewayAttachmentId"))
        for p in r.get("transitGatewayPeeringAttachments") or []:
            pid = p.get("transitGatewayAttachmentId")
            if pid and pid not in seen["tgwpeer"]:
                transit_gateway_peering_attachments.append(p)
                seen["tgwpeer"].add(pid)
        for vp in r.get("vpcPeerings") or []:
            vpid = vp.get("vpcPeeringConnectionId")
            if vpid and vpid not in seen["vpcpeer"]:
                vpc_peerings.append(vp)
                seen["vpcpeer"].add(vpid)
        for v in r.get("vpnConnections") or []:
            if v.get("vpnConnectionId") not in seen["vpnconn"]:
                vpn_connections.append(v)
                seen["vpnconn"].add(v.get("vpnConnectionId"))
        for c in r.get("customerGateways") or []:
            if c.get("customerGatewayId") not in seen["cgw"]:
                customer_gateways.append(c)
                seen["cgw"].add(c.get("customerGatewayId"))
        for tgw_id, routes in (r.get("tgwRouteTables") or {}).items():
            tgw_route_tables.setdefault(tgw_id, routes)
        # Group VPC route tables by VPC ID — same shape the frontend expects
        # (Map<vpcId, VpcRouteTable[]>). Skip route tables with no VPC ID
        # (defensive — every real RT has one).
        for rt in r.get("vpcRouteTables") or []:
            vpc_id = rt.get("vpcId") or ""
            if not vpc_id:
                continue
            vpc_route_tables.setdefault(vpc_id, []).append(rt)
        logger.info(
            "[AWS] Region %s: %d connections, %d VPCs, %d TGWs",
            r.get("region"),
            len(r.get("connections") or []),
            len(r.get("vpcs") or []),
            len(r.get("transitGateways") or []),
        )

    # Infer stub connections for orphan hosted VIFs (mirrors source).
    existing_conn_ids = {c.get("connectionId") for c in connections}
    inferred: List[DxConnection] = []
    inferred_seen: set[str] = set()
    for vif in virtual_interfaces:
        conn_id = vif.get("connectionId")
        if not conn_id or conn_id in existing_conn_ids or conn_id in inferred_seen:
            continue
        inferred_seen.add(conn_id)
        inferred.append(
            {
                "connectionId": conn_id,
                "connectionName": vif.get("virtualInterfaceName")
                or f"Hosted Connection ({conn_id})",
                "connectionState": "available",
                "location": vif.get("location", ""),
                "bandwidth": "",
                "region": vif.get("region", ""),
                "hasBfd": False,
                "awsDeviceV2": vif.get("awsDeviceV2"),
                "awsLogicalDeviceId": vif.get("awsLogicalDeviceId"),
                "isInferred": True,
            }
        )
    effective_connections = connections + inferred if inferred else connections
    if inferred:
        logger.info(
            "[AWS] Inferred %d connection(s) from hosted VIFs (owned=%d)",
            len(inferred),
            len(connections),
        )

    # If everything empty + auth-y errors, raise so the agent gets a clear signal.
    if (
        errors
        and not effective_connections
        and not dx_gateways
        and not vpcs
        and any(_AUTH_RE.search(e) for e in errors)
    ):
        raise RuntimeError(
            "Invalid AWS credentials. Check the Lambda execution role's "
            "policies. First error: " + errors[0]
        )

    # ------- Phase 4.5: CloudWatch BGP + Health events ----------------------
    bgp_prefix_metrics: Dict[str, dict] = {}
    maintenance_events: list = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        bgp_fut = pool.submit(
            cloudwatch_dx.fetch_bgp_prefix_metrics, virtual_interfaces, region
        )
        health_fut = pool.submit(health_dx.fetch_dx_maintenance_events)
        try:
            bgp_prefix_metrics = bgp_fut.result()
        except Exception as err:  # noqa: BLE001
            errors.append(f"BgpPrefixMetrics: {err}")
            bgp_prefix_metrics = {}
        try:
            maintenance_events = health_fut.result()
        except Exception as err:  # noqa: BLE001
            errors.append(f"MaintenanceEvents: {err}")
            maintenance_events = []

    home_account_id = ""
    if transit_gateways:
        home_account_id = transit_gateways[0].get("ownerId", "")
    if not home_account_id:
        try:
            ident = clients.sts().get_caller_identity()
            home_account_id = ident.get("Account", "")
        except Exception as err:  # noqa: BLE001
            errors.append(f"sts:GetCallerIdentity: {err}")

    topology: TopologyData = {
        "connections": effective_connections,
        "virtualInterfaces": virtual_interfaces,
        "dxGateways": dx_gateways,
        "dxGatewayAssociations": dx_gateway_associations,
        "locations": locations,
        "lags": lags,
        "vpcs": vpcs,
        "vpnGateways": vpn_gateways,
        "vpnConnections": vpn_connections,
        "customerGateways": customer_gateways,
        "transitGateways": transit_gateways,
        "transitGatewayAttachments": transit_gateway_attachments,
        "transitGatewayPeeringAttachments": transit_gateway_peering_attachments,
        "vpcPeerings": vpc_peerings,
        "cloudWanCoreNetworks": cloud_wan_core_networks,
        "cloudWanAttachments": cloud_wan_attachments,
        "cloudWanPeerings": cloud_wan_peerings,
        "tgwRouteTables": tgw_route_tables,
        "vpcRouteTables": vpc_route_tables,
        "cloudWanRoutes": cloud_wan_routes,
        "bgpPrefixMetrics": bgp_prefix_metrics,
        "maintenanceEvents": maintenance_events,
        "homeAccountId": home_account_id,
        "regionNames": region_names,
        "fetchErrors": errors,
    }

    logger.info(
        "[AWS] Topology summary: conns=%d vifs=%d dxgws=%d vpcs=%d tgws=%d cloudwan=%d",
        len(effective_connections),
        len(virtual_interfaces),
        len(dx_gateways),
        len(vpcs),
        len(transit_gateways),
        len(cloud_wan_core_networks),
    )

    # ------- Phase 5 (optional): spoke account enrichment -------------------
    if spoke_accounts:
        _enrich_from_spokes(
            topology,
            spoke_accounts,
            cross_account_role_name,
            sorted(discovered) + [region],
            errors,
        )

    # Record errors last (Phase 5 may have added more).
    topology["fetchErrors"] = errors
    return topology


# ----- Per-region fetcher ---------------------------------------------------


def _fetch_region(region: str, errors: List[str]) -> Dict[str, Any]:
    """Mirror of source ``fetchRegion`` — fetches all per-region resources in
    parallel for one region. Returns a dict shaped for Phase 4 merging.
    """
    dx = clients.dx(region)
    ec2c = clients.ec2(region)

    tasks: Dict[str, Callable[[], Any]] = {
        f"{region}/Connections": lambda: direct_connect.fetch_connections(dx),
        f"{region}/VirtualInterfaces": lambda: direct_connect.fetch_virtual_interfaces(
            dx
        ),
        f"{region}/Lags": lambda: direct_connect.fetch_lags(dx),
        f"{region}/Locations": lambda: direct_connect.fetch_locations(dx),
        f"{region}/VPCs": lambda: ec2.fetch_vpcs(ec2c, region),
        f"{region}/VpnGateways": lambda: ec2.fetch_vpn_gateways(ec2c),
        f"{region}/TransitGateways": lambda: ec2.fetch_transit_gateways(ec2c),
        f"{region}/TGWAttachments": lambda: ec2.fetch_transit_gateway_attachments(
            ec2c
        ),
        f"{region}/TGWPeeringAttachments": lambda: ec2.fetch_transit_gateway_peering_attachments(
            ec2c
        ),
        f"{region}/VpcPeerings": lambda: ec2.fetch_vpc_peering_connections(
            ec2c, region
        ),
        f"{region}/VpcRouteTables": lambda: ec2.fetch_vpc_route_tables(ec2c),
        f"{region}/VpnConnections": lambda: ec2.fetch_vpn_connections(ec2c),
        f"{region}/CustomerGateways": lambda: ec2.fetch_customer_gateways(ec2c),
    }
    results = _parallel_logged(tasks, errors, default=[])

    # Fan out TGW route tables per TGW in this region.
    tgw_list = results[f"{region}/TransitGateways"]
    tgw_route_tables: Dict[str, list] = {}
    if tgw_list:
        rt_tasks: Dict[str, Callable[[], Any]] = {
            f"{region}/TGWRoutes({tgw.get('transitGatewayId', '')[-8:]})": (
                (
                    lambda t=tgw: ec2.fetch_tgw_route_tables_with_routes(
                        ec2c, t["transitGatewayId"]
                    )
                )
            )
            for tgw in tgw_list
        }
        rt_results = _parallel_logged(rt_tasks, errors, default=[])
        for tgw in tgw_list:
            tid = tgw.get("transitGatewayId", "")
            key = f"{region}/TGWRoutes({tid[-8:]})"
            routes = rt_results.get(key) or []
            if routes:
                tgw_route_tables[tid] = routes

    return {
        "region": region,
        "connections": results[f"{region}/Connections"],
        "virtualInterfaces": results[f"{region}/VirtualInterfaces"],
        "lags": results[f"{region}/Lags"],
        "locations": results[f"{region}/Locations"],
        "vpcs": results[f"{region}/VPCs"],
        "vpnGateways": results[f"{region}/VpnGateways"],
        "transitGateways": tgw_list,
        "transitGatewayAttachments": results[f"{region}/TGWAttachments"],
        "transitGatewayPeeringAttachments": results[
            f"{region}/TGWPeeringAttachments"
        ],
        "vpcPeerings": results[f"{region}/VpcPeerings"],
        "vpcRouteTables": results[f"{region}/VpcRouteTables"],
        "vpnConnections": results[f"{region}/VpnConnections"],
        "customerGateways": results[f"{region}/CustomerGateways"],
        "tgwRouteTables": tgw_route_tables,
    }


def _empty_region_result(region: str) -> Dict[str, Any]:
    return {
        "region": region,
        "connections": [],
        "virtualInterfaces": [],
        "lags": [],
        "locations": [],
        "vpcs": [],
        "vpnGateways": [],
        "transitGateways": [],
        "transitGatewayAttachments": [],
        "transitGatewayPeeringAttachments": [],
        "vpcPeerings": [],
        "vpcRouteTables": [],
        "vpnConnections": [],
        "customerGateways": [],
        "tgwRouteTables": {},
    }


# ----- DXGW associations fan-out --------------------------------------------


def _fetch_all_dxgw_assocs(
    dx_client: Any, dx_gateways: List[Dict[str, Any]], errors: List[str]
) -> List[Dict[str, Any]]:
    if not dx_gateways:
        return []
    tasks: Dict[str, Callable[[], Any]] = {
        f"DxGwAssoc({g.get('directConnectGatewayId', '')})": (
            (
                lambda gid=g.get(
                    "directConnectGatewayId", ""
                ): direct_connect.fetch_dx_gateway_associations(dx_client, gid)
            )
        )
        for g in dx_gateways
    }
    results = _parallel_logged(tasks, errors, default=[])
    merged: List[Dict[str, Any]] = []
    for lst in results.values():
        merged.extend(lst)
    return merged


# ----- Spoke enrichment -----------------------------------------------------


def _enrich_from_spokes(
    topology: TopologyData,
    spoke_accounts: List[str],
    role_name: str,
    region_list: List[str],
    errors: List[str],
) -> None:
    """Phase 5: AssumeRole into each spoke, fetch VPCs/TGWs/TGWAttachments in
    every discovered region, merge into topology without duplicating.
    """
    existing_vpc_ids = {v.get("vpcId") for v in topology.get("vpcs") or []}
    existing_tgw_ids = {
        t.get("transitGatewayId")
        for t in topology.get("transitGateways") or []
    }
    existing_tgw_att_ids = {
        a.get("transitGatewayAttachmentId")
        for a in topology.get("transitGatewayAttachments") or []
    }
    if "vpcRouteTables" not in topology:
        topology["vpcRouteTables"] = {}  # type: ignore[index]

    enriched_v = enriched_t = enriched_a = enriched_rt = 0
    for account_id in spoke_accounts:
        session = organizations.assume_role_session(account_id, role_name)
        if session is None:
            errors.append(f"AssumeRole({account_id}): failed")
            continue
        for region in region_list:
            ec2c = session.client("ec2", region_name=region)
            try:
                for vpc in ec2.fetch_vpcs(ec2c, region):
                    vpc["ownerAccountId"] = account_id
                    if vpc.get("vpcId") not in existing_vpc_ids:
                        topology["vpcs"].append(vpc)  # type: ignore[index]
                        existing_vpc_ids.add(vpc.get("vpcId"))
                        enriched_v += 1
            except Exception as err:  # noqa: BLE001
                errors.append(f"{account_id}/{region}/VPCs: {err}")
            try:
                for tgw in ec2.fetch_transit_gateways(ec2c):
                    if tgw.get("transitGatewayId") not in existing_tgw_ids:
                        topology["transitGateways"].append(tgw)  # type: ignore[index]
                        existing_tgw_ids.add(tgw.get("transitGatewayId"))
                        enriched_t += 1
            except Exception as err:  # noqa: BLE001
                errors.append(f"{account_id}/{region}/TGWs: {err}")
            try:
                for att in ec2.fetch_transit_gateway_attachments(ec2c):
                    if (
                        att.get("transitGatewayAttachmentId")
                        not in existing_tgw_att_ids
                    ):
                        topology["transitGatewayAttachments"].append(att)  # type: ignore[index]
                        existing_tgw_att_ids.add(
                            att.get("transitGatewayAttachmentId")
                        )
                        enriched_a += 1
            except Exception as err:  # noqa: BLE001
                errors.append(f"{account_id}/{region}/TGWAttachments: {err}")
            try:
                for rt in ec2.fetch_vpc_route_tables(ec2c):
                    vpc_id = rt.get("vpcId") or ""
                    if not vpc_id:
                        continue
                    bucket = topology["vpcRouteTables"].setdefault(vpc_id, [])  # type: ignore[index]
                    existing_rt_ids = {r.get("routeTableId") for r in bucket}
                    if rt.get("routeTableId") not in existing_rt_ids:
                        bucket.append(rt)
                        enriched_rt += 1
            except Exception as err:  # noqa: BLE001
                errors.append(f"{account_id}/{region}/VpcRouteTables: {err}")

    if enriched_v or enriched_t or enriched_a or enriched_rt:
        logger.info(
            "[AWS] Enriched from spoke accounts: %d VPCs, %d TGWs, %d attachments, %d VPC route tables",
            enriched_v,
            enriched_t,
            enriched_a,
            enriched_rt,
        )


# ----- Helpers --------------------------------------------------------------


def _parallel_logged(
    tasks: Dict[str, Callable[[], T]],
    errors: List[str],
    default: Optional[T] = None,
    defaults: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run all tasks in parallel; on failure, append to errors and use the
    per-task default from ``defaults`` or the shared ``default``.

    Mirrors the source's ``Promise.all([logged(...), ...])`` ergonomics.
    """
    if not tasks:
        return {}

    def _safe_call(name: str, fn: Callable[[], T]) -> T:
        try:
            result = fn()
            try:
                count = len(result)  # type: ignore[arg-type]
                logger.info("[AWS] %s: %d items", name, count)
            except TypeError:
                logger.info("[AWS] %s: ok", name)
            return result
        except Exception as err:  # noqa: BLE001
            msg = f"{name}: {err}"
            logger.error("[AWS] %s FAILED: %s", name, err)
            logger.debug("%s", traceback.format_exc())
            errors.append(msg)
            if defaults and name in defaults:
                return defaults[name]  # type: ignore[return-value]
            return default  # type: ignore[return-value]

    max_workers = min(16, max(2, len(tasks)))
    results: Dict[str, Any] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_safe_call, name, fn): name for name, fn in tasks.items()
        }
        for fut in concurrent.futures.as_completed(futures):
            name = futures[fut]
            results[name] = fut.result()
    return results
