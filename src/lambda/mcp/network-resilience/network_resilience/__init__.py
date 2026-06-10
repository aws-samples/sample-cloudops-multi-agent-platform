"""Shared network-resilience module.

Imported by two Lambdas that ship this package copied into their zips
(see Makefile packaging step):

1. ``src/lambda/mcp/network-resilience/`` — MCP tool Lambda called by the
   network-resiliency-agent worker via AgentCore Gateway SigV4.
2. ``src/lambda/network-resilience-api/`` — REST API Lambda called by the
   browser (Phase 4) for fast client-side operations that bypass the chat
   flow (target-tier re-assessment, live BGP polling, cross-account enrich).

Single source of truth for:
- ``types`` — TypedDicts matching the source ``dx-visualizer/src/types/``.
- ``topology`` — AWS discovery (Phase 1 scaffold; Phase 1 follow-up fills in
  per-API fetchers).
- ``engine`` — resiliency + best-practice rules + scoring + DX pricing
  (Phase 2 populates this subtree).
"""

__version__ = "0.1.0"
