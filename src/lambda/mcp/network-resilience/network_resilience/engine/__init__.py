"""Rules engine — Phase 2 populates this subtree.

Will hold:
- ``resiliency_rules`` (5 rules emitting ghost nodes)
- ``bestpractice_rules`` (17 checks + 2 SLA attestations)
- ``recommendation_engine`` (orchestrator + per-DXGW scoring)
- ``sla_gating`` (location/device redundancy checks)
- ``pricing`` (DX port pricing formulas, hardcoded)

Intentionally empty in Phase 1 so the topology fetch path can be smoke-tested
before rule porting begins.
"""
