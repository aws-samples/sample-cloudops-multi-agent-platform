"""Best-practice rules.

Python port of source ``dx-visualizer/src/engine/bestpractice-rules.ts`` (646 lines).

17 operational checks + 2 SLA-precondition attestations. Each returns a
``RuleResult`` with optional node annotations and a single recommendation.
``get_all_bestpractice_results`` aggregates them and sorts by severity
(critical → warning → info).

Rule preservation rules (non-negotiable):
- Rule IDs, severity labels, and title/description strings are byte-identical
  to the source. Tests snapshot against these exact values.
- BGP route-limit thresholds: hard=100, caution=80.
- The "all guidance rules skip when topology is empty" pattern is preserved
  so empty-topology callers don't get a firehose of general advice.
"""

from __future__ import annotations

from typing import List, NamedTuple, Optional

from ..types import (
    NodeAnnotation,
    Recommendation,
    ResiliencyLevel,
    TopologyData,
)


class RuleResult(NamedTuple):
    annotations: List[NodeAnnotation]
    recommendation: Optional[Recommendation]


# Sentinel for "no rule emitted" — keeps call sites tidy.
_EMPTY: RuleResult = RuleResult(annotations=[], recommendation=None)


# ----- SLA-precondition attestations (can't be detected via API) -----------


def rule_enterprise_support_required(
    topology: TopologyData,
    current_level: ResiliencyLevel,
    target_level: Optional[ResiliencyLevel] = None,
) -> RuleResult:
    """99.9% and 99.99% DX SLAs both require Enterprise Support.

    Source: https://aws.amazon.com/directconnect/sla/
    """
    levels: List[ResiliencyLevel] = [current_level]
    if target_level:
        levels.append(target_level)
    if not any(l in ("high", "maximum") for l in levels):
        return _EMPTY
    if not (topology.get("connections") or []) and not (
        topology.get("virtualInterfaces") or []
    ):
        return _EMPTY
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-enterprise-support",
            "ruleId": "enterprise-support-required",
            "category": "bestpractice",
            "severity": "info",
            "title": "Verify Enterprise Support plan is in place",
            "description": (
                "Required for the 99.9% and 99.99% Direct Connect SLAs. "
                "See https://aws.amazon.com/directconnect/sla/"
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


def rule_well_architected_review_required(
    topology: TopologyData,
    current_level: ResiliencyLevel,
    target_level: Optional[ResiliencyLevel] = None,
) -> RuleResult:
    """99.99% DX SLA additionally requires a Well-Architected Review."""
    levels: List[ResiliencyLevel] = [current_level]
    if target_level:
        levels.append(target_level)
    if "maximum" not in levels:
        return _EMPTY
    if not (topology.get("connections") or []) and not (
        topology.get("virtualInterfaces") or []
    ):
        return _EMPTY
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-well-architected-review",
            "ruleId": "well-architected-review-required",
            "category": "bestpractice",
            "severity": "info",
            "title": "Verify Well-Architected Review has been completed",
            "description": (
                "Required for the 99.99% Direct Connect SLA, in addition to "
                "Enterprise Support. See https://aws.amazon.com/directconnect/sla/"
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


# ----- Detection rules (run on every topology) ------------------------------


def rule_bfd_guidance(topology: TopologyData) -> RuleResult:
    """BFD state isn't exposed by the API — general guidance."""
    if not (topology.get("connections") or []) and not (
        topology.get("virtualInterfaces") or []
    ):
        return _EMPTY
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-bfd-guidance",
            "ruleId": "bfd-guidance",
            "category": "bestpractice",
            "severity": "info",
            "title": "Ensure Bidirectional Forwarding Detection (BFD) is Enabled",
            "description": (
                "Without BFD, failover relies on BGP hold timers, which can "
                "take up to 90 seconds to detect a link failure. BFD reduces "
                "detection to under a second. Configure BFD with a minimum "
                "interval of 300 ms and a liveness-detection multiplier of 3, "
                "and disable BGP graceful restart so BFD-driven failover is "
                "not delayed. BFD status is not available via the AWS API — "
                "verify it is enabled on your customer router configuration. "
                "See https://repost.aws/knowledge-center/enable-bfd-direct-connect"
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


def rule_vif_down(topology: TopologyData) -> RuleResult:
    """Any VIF where state != 'available' OR all BGP peers are down."""
    down_vifs: List[str] = []
    for vif in topology.get("virtualInterfaces") or []:
        vif_down = vif.get("virtualInterfaceState") != "available"
        bgp_peers = vif.get("bgpPeers") or []
        all_bgp_down = (
            len(bgp_peers) > 0
            and all(p.get("bgpStatus") != "up" for p in bgp_peers)
        )
        if vif_down or all_bgp_down:
            down_vifs.append(
                vif.get("virtualInterfaceName") or vif.get("virtualInterfaceId", "")
            )
    if not down_vifs:
        return _EMPTY
    this_path_word = "this path" if len(down_vifs) == 1 else "these paths"
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-vif-down",
            "ruleId": "vif-down",
            "category": "bestpractice",
            "severity": "critical",
            "title": "Virtual Interface(s) in DOWN State",
            "description": (
                f"BGP is down on {', '.join(down_vifs)} — no traffic can "
                f"flow over {this_path_word}. Check the BGP configuration, "
                "VLAN tagging, and physical connectivity."
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


def rule_connection_not_available(topology: TopologyData) -> RuleResult:
    bad_conns: List[str] = []
    for conn in topology.get("connections") or []:
        if conn.get("connectionState") != "available":
            bad_conns.append(
                conn.get("connectionName") or conn.get("connectionId", "")
            )
    if not bad_conns:
        return _EMPTY
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-connection-not-available",
            "ruleId": "connection-not-available",
            "category": "bestpractice",
            "severity": "critical",
            "title": "Direct Connect Connection(s) Not Available",
            "description": (
                f"{len(bad_conns)} connection(s) are not in \"available\" state: "
                f"{', '.join(bad_conns)}. These connections are not passing "
                "traffic. Check the AWS Console for provisioning status or errors."
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


def rule_no_vpn_backup(topology: TopologyData) -> RuleResult:
    if not (topology.get("connections") or []) and not (
        topology.get("virtualInterfaces") or []
    ):
        return _EMPTY
    if topology.get("vpnConnections") or []:
        return _EMPTY
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-no-vpn-backup",
            "ruleId": "no-vpn-backup",
            "category": "bestpractice",
            "severity": "warning",
            "title": "No Site-to-Site VPN Backup",
            "description": (
                "No Site-to-Site VPN connections detected alongside Direct "
                "Connect. AWS recommends configuring a VPN connection as a "
                "backup path so that if Direct Connect is entirely unavailable "
                "(e.g., fiber cut or location outage), traffic can fail over "
                "to the internet-based VPN tunnel. Note: a VPN backup does not "
                "improve the Direct Connect SLA — it only provides a failover "
                "path, useful for budget-constrained deployments that can't "
                "justify a second Direct Connect."
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


def rule_cross_region_path(topology: TopologyData) -> RuleResult:
    """DX region differs from resource region — SLA only covers the DX segment."""
    dx_regions = {
        c.get("region") for c in topology.get("connections") or [] if c.get("region")
    }
    if not dx_regions:
        return _EMPTY
    resource_regions: set[str] = {
        v.get("region") for v in topology.get("vpcs") or [] if v.get("region")
    }
    for tgw in topology.get("transitGateways") or []:
        arn = tgw.get("transitGatewayArn", "")
        parts = arn.split(":")
        # arn:aws:ec2:us-east-1:123:transit-gateway/... — region is 4th field
        if len(parts) > 3 and parts[3]:
            resource_regions.add(parts[3])
    if not resource_regions:
        return _EMPTY
    uncovered = [r for r in resource_regions if r not in dx_regions]
    if not uncovered:
        return _EMPTY
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-cross-region-path",
            "ruleId": "cross-region-path",
            "category": "bestpractice",
            "severity": "info",
            "title": "Cross-Region Network Path Detected",
            "description": (
                f"Your Direct Connect connections terminate in "
                f"{', '.join(sorted(dx_regions))} but resources exist in "
                f"{', '.join(sorted(uncovered))}. The DX SLA covers the "
                "connection segment between your on-premises network and "
                "the AWS DX location. Traffic routed cross-region over the "
                "AWS backbone has separate availability characteristics and "
                "is not covered by the Direct Connect SLA. Consider "
                "provisioning Direct Connect connections in each resource "
                "region for end-to-end SLA coverage."
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


def rule_sla_awareness(topology: TopologyData) -> RuleResult:
    if not (topology.get("connections") or []) and not (
        topology.get("virtualInterfaces") or []
    ):
        return _EMPTY
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-sla-awareness",
            "ruleId": "sla-awareness",
            "category": "bestpractice",
            "severity": "info",
            "title": "Understand Direct Connect SLA tiers",
            "description": (
                "AWS publishes three Direct Connect SLA tiers: Single "
                "Connection (95%), Multi-Site Non-Redundant / High Resiliency "
                "(99.9%, 2+ locations), and Multi-Site Redundant / Maximum "
                "Resiliency (99.99%, 2+ locations with 2+ devices each). Only "
                "the Maximum Resiliency model qualifies for the highest SLA. "
                "See https://aws.amazon.com/directconnect/sla/"
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


def rule_resiliency_toolkit(topology: TopologyData) -> RuleResult:
    if not (topology.get("connections") or []) and not (
        topology.get("virtualInterfaces") or []
    ):
        return _EMPTY
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-resiliency-toolkit",
            "ruleId": "resiliency-toolkit",
            "category": "bestpractice",
            "severity": "info",
            "title": (
                "Use the Direct Connect Resiliency Toolkit for production workloads"
            ),
            "description": (
                "For production or mission-critical workloads, implement the "
                "High Resiliency or Maximum Resiliency model using the AWS "
                "Direct Connect Resiliency Toolkit so traffic keeps flowing "
                "during a maintenance event. The Development and Test model is "
                "a more cost-efficient fit for non-production workloads. See "
                "https://docs.aws.amazon.com/directconnect/latest/UserGuide/"
                "resiliency_toolkit.html and "
                "https://docs.aws.amazon.com/directconnect/latest/UserGuide/"
                "dx-maintenance.html"
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


def rule_consistent_prefix_advertisement(topology: TopologyData) -> RuleResult:
    if len(topology.get("virtualInterfaces") or []) < 2:
        return _EMPTY
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-consistent-prefix-advertisement",
            "ruleId": "consistent-prefix-advertisement",
            "category": "bestpractice",
            "severity": "info",
            "title": "Advertise the same prefixes across redundant VIFs",
            "description": (
                "Validate that the same BGP prefixes are learned and "
                "advertised across redundant Virtual Interfaces. Asymmetric "
                "advertisement leaves the failover path with different "
                "reachability and can cause traffic blackholes during a "
                "failover. BGP route state is not available via the AWS API — "
                "verify from your customer router."
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


# AWS accepts at most 100 prefixes per BGP session on private/transit VIFs.
# Public VIFs have a higher limit (1000) and are excluded from this check.
_BGP_ROUTE_HARD_LIMIT = 100
_BGP_ROUTE_CAUTION_THRESHOLD = 80


def rule_bgp_route_limit(topology: TopologyData) -> RuleResult:
    """Four-way branch based on accepted-prefix counts from CloudWatch:
        over hard limit → critical
        over caution threshold → warning
        all healthy → info (positive "met" check)
        unknown (no CloudWatch metrics) → info guidance
    """
    vifs = topology.get("virtualInterfaces") or []
    applicable = [
        v
        for v in vifs
        if v.get("virtualInterfaceType") in ("private", "transit")
    ]
    if not applicable:
        return _EMPTY

    metrics = topology.get("bgpPrefixMetrics") or {}
    over: List[str] = []
    near: List[str] = []
    healthy: List[tuple[str, int]] = []
    unknown: List[str] = []

    for vif in applicable:
        vif_id = vif.get("virtualInterfaceId", "")
        label = vif.get("virtualInterfaceName") or vif_id
        accepted = (metrics.get(vif_id) or {}).get("accepted")
        if accepted is None:
            unknown.append(label)
        elif accepted >= _BGP_ROUTE_HARD_LIMIT:
            over.append(f"{label} ({accepted} accepted)")
        elif accepted >= _BGP_ROUTE_CAUTION_THRESHOLD:
            near.append(f"{label} ({accepted} accepted)")
        else:
            healthy.append((label, accepted))

    if over:
        return RuleResult(
            annotations=[],
            recommendation={
                "id": "bp-bgp-route-limit",
                "ruleId": "bgp-route-limit",
                "category": "bestpractice",
                "severity": "critical",
                "title": "BGP route limit reached — session at risk of teardown",
                "description": (
                    "The following VIFs are at or above the 100-prefix limit "
                    "for on-premises → AWS advertisement: "
                    f"{', '.join(over)}. Exceeding the limit causes BGP "
                    "session teardown and network disconnection. Summarize "
                    "or filter on-premises routes immediately. See "
                    "https://docs.aws.amazon.com/directconnect/latest/UserGuide/limits.html"
                ),
                "additionalNodes": [],
                "additionalEdges": [],
            },
        )
    if near:
        return RuleResult(
            annotations=[],
            recommendation={
                "id": "bp-bgp-route-limit",
                "ruleId": "bgp-route-limit",
                "category": "bestpractice",
                "severity": "warning",
                "title": "BGP routes approaching the 100-prefix limit",
                "description": (
                    "The following VIFs are within 20 prefixes of the "
                    "100-prefix hard limit: "
                    f"{', '.join(near)}. Plan summarization now so "
                    "on-premises growth does not trigger a BGP session "
                    "teardown. See "
                    "https://docs.aws.amazon.com/directconnect/latest/UserGuide/limits.html"
                ),
                "additionalNodes": [],
                "additionalEdges": [],
            },
        )
    if healthy and not unknown:
        max_count = max(count for _, count in healthy)
        count_count = len(healthy)
        verb = "are well under" if count_count > 1 else "is well under"
        plural_s = "s" if count_count > 1 else ""
        prefix_plural = "" if max_count == 1 else "es"
        return RuleResult(
            annotations=[],
            recommendation={
                "id": "bp-bgp-route-limit",
                "ruleId": "bgp-route-limit-ok",
                "category": "bestpractice",
                "severity": "info",
                "title": "BGP routes within the 100-prefix limit",
                "description": (
                    f"All {count_count} private/transit VIF{plural_s} {verb} "
                    f"the 100-prefix hard limit — peak observed is "
                    f"{max_count} prefix{prefix_plural} accepted from on-premises."
                ),
                "additionalNodes": [],
                "additionalEdges": [],
            },
        )

    this_vif_word = (
        "this VIF" if len(unknown) == 1 else f"these {len(unknown)} VIFs"
    )
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-bgp-route-limit",
            "ruleId": "bgp-route-limit",
            "category": "bestpractice",
            "severity": "info",
            "title": "Keep BGP routes under 100 per session",
            "description": (
                "Private and transit Virtual Interfaces accept at most 100 "
                "routes per BGP session from on-premises to AWS. CloudWatch "
                f"prefix metrics were not available for {this_vif_word} "
                f"({', '.join(unknown)}) — verify the count directly on your "
                "customer router and summarize routes if needed. See "
                "https://docs.aws.amazon.com/directconnect/latest/UserGuide/limits.html"
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


def rule_vpn_tunnel_redundancy(topology: TopologyData) -> RuleResult:
    vpn_conns = topology.get("vpnConnections") or []
    if not vpn_conns:
        return _EMPTY
    degraded: List[str] = []
    for vpn in vpn_conns:
        up = sum(1 for t in (vpn.get("tunnels") or []) if t.get("status") == "UP")
        if up < 2:
            degraded.append(f"{vpn.get('vpnConnectionId', '')} ({up}/2 tunnels UP)")
    if not degraded:
        return _EMPTY
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-vpn-tunnel-redundancy",
            "ruleId": "vpn-tunnel-redundancy",
            "category": "bestpractice",
            "severity": "warning",
            "title": "Ensure both VPN tunnels are UP for redundancy",
            "description": (
                "Each Site-to-Site VPN connection provides two tunnels for "
                "redundancy. The following connection(s) do not have both "
                f"tunnels UP: {', '.join(degraded)}. Investigate the customer "
                "gateway configuration and the tunnel health to restore "
                "redundancy."
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


def rule_cgw_redundancy(topology: TopologyData) -> RuleResult:
    vpn_conns = topology.get("vpnConnections") or []
    if not vpn_conns:
        return _EMPTY
    cgw_ids = {v.get("customerGatewayId") for v in vpn_conns if v.get("customerGatewayId")}
    if len(cgw_ids) >= 2:
        return _EMPTY
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-cgw-redundancy",
            "ruleId": "cgw-redundancy",
            "category": "bestpractice",
            "severity": "warning",
            "title": "Deploy multiple customer gateways for device redundancy",
            "description": (
                "All Site-to-Site VPN connections terminate on the same "
                "customer gateway. A single customer gateway (or DX partner "
                "device) is a single point of failure — deploy at least two "
                "CGWs so device failures do not take down the hybrid network."
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


def rule_dx_partner_diversity(topology: TopologyData) -> RuleResult:
    conns = topology.get("connections") or []
    if len(conns) < 2:
        return _EMPTY
    partners = {
        c.get("partnerName")
        for c in conns
        if c.get("partnerName") and c.get("partnerName", "").strip()
    }
    # If no named partners, we can't tell — stay silent.
    if not partners or len(partners) >= 2:
        return _EMPTY
    partner_name = next(iter(partners))
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-dx-partner-diversity",
            "ruleId": "dx-partner-diversity",
            "category": "bestpractice",
            "severity": "info",
            "title": "Consider sourcing Direct Connect from multiple partners",
            "description": (
                "All Direct Connect connections are sourced from the same "
                f"partner/last-mile provider ({partner_name}). If budget "
                "allows, procuring Direct Connect from multiple partners "
                "minimizes single-point-of-failure risk on the partner side "
                "(partner network outages, partner maintenance events)."
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


def rule_vpn_dpd(topology: TopologyData) -> RuleResult:
    vpn_conns = topology.get("vpnConnections") or []
    if not vpn_conns:
        return _EMPTY
    no_action_tunnels: List[str] = []
    for vpn in vpn_conns:
        for t in vpn.get("tunnels") or []:
            if t.get("dpdTimeoutAction") == "none":
                label = t.get("outsideIpAddress") or "tunnel"
                no_action_tunnels.append(
                    f"{vpn.get('vpnConnectionId', '')} ({label})"
                )
    if no_action_tunnels:
        return RuleResult(
            annotations=[],
            recommendation={
                "id": "bp-vpn-dpd",
                "ruleId": "vpn-dpd",
                "category": "bestpractice",
                "severity": "warning",
                "title": "Enable DPD timeout action on VPN tunnels",
                "description": (
                    "The following VPN tunnel(s) are configured with "
                    "DpdTimeoutAction=none, so AWS takes no action when the "
                    "customer gateway stops responding to DPD probes: "
                    f"{', '.join(no_action_tunnels)}. Switch the tunnel "
                    "option to \"clear\" or \"restart\" (via "
                    "ModifyVpnTunnelOptions) so failover is not delayed after "
                    "a peer failure. Also verify DPD is configured on the "
                    "customer gateway side — that half is not visible via "
                    "the AWS API."
                ),
                "additionalNodes": [],
                "additionalEdges": [],
            },
        )
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-vpn-dpd",
            "ruleId": "vpn-dpd",
            "category": "bestpractice",
            "severity": "info",
            "title": "Verify VPN Dead Peer Detection (DPD) on the customer gateway",
            "description": (
                "AWS-side DPD is configured on every tunnel (DpdTimeoutAction "
                "is set to clear or restart). Verify DPD is also configured "
                "on the customer gateway so failed tunnels are detected "
                "quickly from both sides — customer-gateway DPD config is "
                "not exposed via the AWS API."
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


def rule_dx_location_redundancy(topology: TopologyData) -> RuleResult:
    if not (topology.get("connections") or []) and not (
        topology.get("virtualInterfaces") or []
    ):
        return _EMPTY
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-dx-location-redundancy",
            "ruleId": "dx-location-redundancy",
            "category": "bestpractice",
            "severity": "info",
            "title": "Choose DX location redundancy that matches your risk profile",
            "description": (
                "Metro diversity (DX locations in the same metro) gives fast, "
                "low-latency failover and protects against single-facility "
                "failures (power, cooling, fiber cut to one building) at lower "
                "cross-connect cost. Geographic diversity (DX locations in "
                "separate regions) protects against large-scale regional "
                "events (natural disasters, metro-wide fiber cuts, grid "
                "outages) at the cost of higher latency on the backup path "
                "and higher circuit costs. Metro diversity is often sufficient "
                "for high availability; choose geographic diversity when "
                "business continuity requires resilience against catastrophic "
                "regional events."
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


def rule_bgp_timers_fallback(topology: TopologyData) -> RuleResult:
    if not (topology.get("virtualInterfaces") or []):
        return _EMPTY
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-bgp-timers-fallback",
            "ruleId": "bgp-timers-fallback",
            "category": "bestpractice",
            "severity": "info",
            "title": "Optimize BGP timers when BFD is not supported",
            "description": (
                "If the customer gateway or partner device does not support "
                "BFD, tune the BGP hold timer down to roughly 20–30 seconds "
                "to reduce failure detection time while still keeping the "
                "session stable. The AWS default hold timer is 90 seconds, "
                "which delays failover significantly."
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


def rule_dx_failover_testing(topology: TopologyData) -> RuleResult:
    if not (topology.get("connections") or []) and not (
        topology.get("virtualInterfaces") or []
    ):
        return _EMPTY
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-dx-failover-testing",
            "ruleId": "dx-failover-testing",
            "category": "bestpractice",
            "severity": "info",
            "title": "Conduct regular Direct Connect failover tests",
            "description": (
                "Exercise your redundant paths on a schedule. AWS allows you "
                "to temporarily shut down BGP peers on your VIFs from the AWS "
                "side for up to 72 hours, which lets you simulate router "
                "maintenance and validate failover before it happens for "
                "real. Note: partner-provided / hosted VIFs may be under "
                "partner monitoring — coordinate with your DX partner before "
                "running failover tests."
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


def rule_failover_runbooks(topology: TopologyData) -> RuleResult:
    if not (topology.get("connections") or []) and not (
        topology.get("virtualInterfaces") or []
    ):
        return _EMPTY
    return RuleResult(
        annotations=[],
        recommendation={
            "id": "bp-failover-runbooks",
            "ruleId": "failover-runbooks",
            "category": "bestpractice",
            "severity": "info",
            "title": "Maintain documented DX/VPN failover runbooks",
            "description": (
                "Create and maintain operational runbooks for Direct Connect "
                "and VPN failover procedures, including escalation paths, "
                "on-call rotations, and partner coordination steps. During "
                "an incident, a well-tested runbook is what turns failover "
                "from a multi-hour scramble into a repeatable procedure."
            ),
            "additionalNodes": [],
            "additionalEdges": [],
        },
    )


# ----- Aggregator -----------------------------------------------------------


_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def get_all_bestpractice_results(topology: TopologyData) -> dict:
    """Run every best-practice rule and return sorted annotations + recommendations.

    Matches source ``getAllBestPracticeResults`` shape — returns
    ``{"annotations": [...], "recommendations": [...]}``.
    """
    rule_fns = [
        rule_bfd_guidance,
        rule_vif_down,
        rule_connection_not_available,
        rule_no_vpn_backup,
        rule_cross_region_path,
        rule_sla_awareness,
        rule_resiliency_toolkit,
        rule_consistent_prefix_advertisement,
        rule_bgp_route_limit,
        rule_vpn_tunnel_redundancy,
        rule_cgw_redundancy,
        rule_dx_partner_diversity,
        rule_vpn_dpd,
        rule_dx_location_redundancy,
        rule_bgp_timers_fallback,
        rule_dx_failover_testing,
        rule_failover_runbooks,
    ]

    all_annotations: List[NodeAnnotation] = []
    all_recommendations: List[Recommendation] = []
    for fn in rule_fns:
        result = fn(topology)
        all_annotations.extend(result.annotations)
        if result.recommendation is not None:
            all_recommendations.append(result.recommendation)

    all_recommendations.sort(
        key=lambda r: _SEVERITY_ORDER.get(r.get("severity", ""), 3)
    )
    return {
        "annotations": all_annotations,
        "recommendations": all_recommendations,
    }
