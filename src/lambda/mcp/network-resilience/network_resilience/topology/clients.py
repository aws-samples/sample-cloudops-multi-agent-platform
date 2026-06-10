"""boto3 client factories.

Python equivalent of source ``dx-visualizer/src/api/aws-client.ts``. In the
Lambda execution environment the role's credentials are picked up
automatically; callers just pass ``region``. Global-API clients (Organizations,
NetworkManager, Health, IAM, Pricing) ignore the caller's region and use the
service's canonical endpoint.
"""

from __future__ import annotations

from typing import Any, Optional

import boto3
from botocore.config import Config

# 15s per-request timeout to match the source SDK config. Total topology
# fetch wall time is bounded by Lambda timeout (60s, see tools.json), so a
# tight per-request cap is important to keep partial-failure mode snappy.
_CLIENT_CONFIG = Config(
    connect_timeout=5,
    read_timeout=15,
    retries={"max_attempts": 2, "mode": "standard"},
)


def _client(service: str, region: Optional[str] = None) -> Any:
    return boto3.client(service, region_name=region, config=_CLIENT_CONFIG)


def dx(region: str) -> Any:
    return _client("directconnect", region)


def ec2(region: str) -> Any:
    return _client("ec2", region)


def cloudwatch(region: str) -> Any:
    return _client("cloudwatch", region)


def ssm(region: str) -> Any:
    return _client("ssm", region)


def sts(region: Optional[str] = None) -> Any:
    return _client("sts", region)


# ----- Global-endpoint services --------------------------------------------


def networkmanager() -> Any:
    """NetworkManager (Cloud WAN) is global — pinned to us-west-2."""
    return _client("networkmanager", "us-west-2")


def organizations() -> Any:
    """Organizations is global — pinned to us-east-1."""
    return _client("organizations", "us-east-1")


def health() -> Any:
    """AWS Health API is global — pinned to us-east-1 (Active endpoint)."""
    return _client("health", "us-east-1")


def iam() -> Any:
    """IAM is global — use us-east-1."""
    return _client("iam", "us-east-1")


def pricing() -> Any:
    """AWS Pricing API is pinned to us-east-1."""
    return _client("pricing", "us-east-1")
