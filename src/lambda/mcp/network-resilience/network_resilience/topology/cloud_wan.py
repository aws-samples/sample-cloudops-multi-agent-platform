"""Cloud WAN (NetworkManager) API fetchers.

Python port of source ``dx-visualizer/src/api/cloud-wan.ts``. NetworkManager
is a global API and must be called from us-west-2 (see ``clients.networkmanager``).

Cloud WAN is optional data — on deserialization or permission failures we log
and return empty so the rest of the topology keeps flowing.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..types import (
    CloudWanAttachment,
    CloudWanCoreNetwork,
    CloudWanPeering,
    CloudWanRoute,
    CloudWanSegmentRoutes,
)

logger = logging.getLogger(__name__)


def _tags_to_record(tags: List[Dict[str, str]] | None) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for t in tags or []:
        key = t.get("Key")
        if key:
            out[key] = t.get("Value", "") or ""
    return out


def _normalize_attachment_type(raw: str | None) -> str:
    """Source normalizes attachment types to lowercase + hyphen-separated
    (``VPC`` → ``vpc``, ``SITE_TO_SITE_VPN`` → ``site-to-site-vpn``)."""
    return (raw or "VPC").lower().replace("_", "-")


def fetch_core_networks(nm_client: Any) -> List[CloudWanCoreNetwork]:
    """List core networks, then fetch detail for each."""
    list_res = nm_client.list_core_networks()
    summaries = list_res.get("CoreNetworks") or []

    out: List[CloudWanCoreNetwork] = []
    for summary in summaries:
        cn_id = summary.get("CoreNetworkId")
        if not cn_id:
            continue
        try:
            detail_res = nm_client.get_core_network(CoreNetworkId=cn_id)
        except Exception as err:  # noqa: BLE001
            logger.warning(
                "[CloudWAN] get_core_network(%s) failed: %s", cn_id, err
            )
            continue
        cn = detail_res.get("CoreNetwork") or {}
        out.append(
            {
                "coreNetworkId": cn.get("CoreNetworkId", ""),
                "coreNetworkArn": cn.get("CoreNetworkArn", ""),
                "globalNetworkId": cn.get("GlobalNetworkId", ""),
                "description": cn.get("Description", ""),
                "state": (cn.get("State") or "").lower(),
                "edges": [
                    {
                        "edgeLocation": e.get("EdgeLocation", ""),
                        "asn": e.get("Asn", 0),
                        "insideCidrBlocks": e.get("InsideCidrBlocks") or [],
                    }
                    for e in (cn.get("Edges") or [])
                ],
                "segments": [
                    {
                        "name": s.get("Name", ""),
                        "edgeLocations": s.get("EdgeLocations") or [],
                        "sharedSegments": s.get("SharedSegments") or [],
                    }
                    for s in (cn.get("Segments") or [])
                ],
            }
        )
    return out


def fetch_cloud_wan_attachments(nm_client: Any) -> List[CloudWanAttachment]:
    """Deserialization errors from control-character-laden tags are swallowed
    with a warning — Cloud WAN is optional topology context.
    """
    try:
        res = nm_client.list_attachments()
    except Exception as err:  # noqa: BLE001
        logger.warning(
            "[CloudWAN] list_attachments failed, returning empty list: %s", err
        )
        return []
    return [
        {
            "attachmentId": a.get("AttachmentId", ""),
            "coreNetworkId": a.get("CoreNetworkId", ""),
            "ownerAccountId": a.get("OwnerAccountId", ""),
            "attachmentType": _normalize_attachment_type(
                a.get("AttachmentType")
            ),
            "edgeLocation": a.get("EdgeLocation", ""),
            "resourceArn": a.get("ResourceArn", ""),
            "segmentName": a.get("SegmentName", ""),
            "state": (a.get("State") or "").lower(),
            "tags": _tags_to_record(a.get("Tags")),
        }
        for a in (res.get("Attachments") or [])
    ]


def fetch_cloud_wan_peerings(nm_client: Any) -> List[CloudWanPeering]:
    try:
        res = nm_client.list_peerings()
    except Exception as err:  # noqa: BLE001
        logger.warning("[CloudWAN] list_peerings failed: %s", err)
        return []
    return [
        {
            "peeringId": p.get("PeeringId", ""),
            "coreNetworkId": p.get("CoreNetworkId", ""),
            "peeringType": p.get("PeeringType", ""),
            "edgeLocation": p.get("EdgeLocation", ""),
            "resourceArn": p.get("ResourceArn", ""),
            "state": (p.get("State") or "").lower(),
            "tags": _tags_to_record(p.get("Tags")),
        }
        for p in (res.get("Peerings") or [])
    ]


def fetch_cloud_wan_routes(
    nm_client: Any, core_networks: List[CloudWanCoreNetwork]
) -> Dict[str, List[CloudWanSegmentRoutes]]:
    """Fan out over (core_network × segment × edge_location) to fetch routes.
    Per-(segment, edge) failures are logged and skipped.
    """
    route_map: Dict[str, List[CloudWanSegmentRoutes]] = {}
    for cn in core_networks:
        segment_routes: List[CloudWanSegmentRoutes] = []
        for segment in cn.get("segments") or []:
            seg_name = segment.get("name", "")
            for edge_location in segment.get("edgeLocations") or []:
                try:
                    res = nm_client.get_network_routes(
                        GlobalNetworkId=cn.get("globalNetworkId", ""),
                        RouteTableIdentifier={
                            "CoreNetworkSegmentEdge": {
                                "CoreNetworkId": cn.get("coreNetworkId", ""),
                                "SegmentName": seg_name,
                                "EdgeLocation": edge_location,
                            }
                        },
                    )
                except Exception as err:  # noqa: BLE001
                    logger.warning(
                        "[CloudWAN] get_network_routes segment=%s edge=%s: %s",
                        seg_name,
                        edge_location,
                        err,
                    )
                    continue
                routes: List[CloudWanRoute] = [
                    {
                        "destinationCidrBlock": r.get(
                            "DestinationCidrBlock", ""
                        ),
                        "destinations": [
                            {
                                "coreNetworkAttachmentId": d.get(
                                    "CoreNetworkAttachmentId", ""
                                ),
                                "segmentName": d.get("SegmentName", ""),
                                "edgeLocation": d.get("EdgeLocation", ""),
                                "resourceType": d.get("ResourceType", ""),
                                "resourceId": d.get("ResourceId", ""),
                            }
                            for d in (r.get("Destinations") or [])
                        ],
                        "type": (r.get("Type") or "PROPAGATED").lower(),
                        "state": (r.get("State") or "ACTIVE").lower(),
                    }
                    for r in (res.get("NetworkRoutes") or [])
                ]
                segment_routes.append(
                    {
                        "segmentName": seg_name,
                        "edgeLocation": edge_location,
                        "routes": routes,
                    }
                )
        route_map[cn.get("coreNetworkId", "")] = segment_routes
    return route_map
