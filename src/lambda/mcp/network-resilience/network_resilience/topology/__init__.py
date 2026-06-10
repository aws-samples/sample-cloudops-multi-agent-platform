"""Topology discovery — live AWS and mock.

Entry point: ``fetch.fetch_all_topology_data(default_region, mock_scenario)``.

Phase 1 scaffold lands the 5-phase orchestrator shape and mock scenario
loader; live per-API fetchers (direct_connect, ec2, cloud_wan, cloudwatch_dx,
health_dx, regions, organizations) land as a Phase 1 follow-up so the tool
can be deployed and smoke-tested against the gateway immediately.
"""
