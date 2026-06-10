"""Cross-account AWS session management for MCP Lambda functions.

Two scenarios are supported:

1. **Static targets, known at deploy time.** The Lambda reads
   ``CROSS_ACCOUNT_ROLE_ARN`` (or ``CROSS_ACCOUNT_ROLE_ARN_<ALIAS>``
   when a single Lambda needs multiple named roles) from its env and
   assumes that role once per cold start. ``get_aws_client(service,
   role_alias=...)`` is the main entry point.

2. **Dynamic spoke accounts, discovered at runtime.** Same role name is
   provisioned in many accounts (e.g. via a CloudFormation StackSet over
   the Organization). ``assume_role_for_account(account_id, role_name)``
   builds the ARN from a template at call time and caches the session
   per ``(account_id, role_name)`` pair.

Both paths degrade gracefully: if no env var is set (Scenario 1) or the
AssumeRole call fails (Scenario 2), Scenario 1 falls back to the Lambda
execution role and Scenario 2 returns ``None`` so the caller can skip
that account without aborting the broader discovery.

Env vars:

    CROSS_ACCOUNT_ROLE_ARN              — unnamed/default role ARN
    CROSS_ACCOUNT_ROLE_ARN_<ALIAS>      — aliased role ARN (Scenario 1 multi-target)
    CROSS_ACCOUNT_EXTERNAL_ID           — external ID for default
    CROSS_ACCOUNT_EXTERNAL_ID_<ALIAS>   — aliased external ID

Usage::

    from shared.cross_account import get_aws_client, assume_role_for_account

    ce = get_aws_client("ce")                              # Scenario 1, default role
    coh = get_aws_client("cost-optimization-hub",           # Scenario 1, aliased
                         role_name_="us-east-1",
                         role_alias="COH")
    # Scenario 2
    ec2 = assume_role_for_account(account_id, "NetworkReadOnlyRole", "ec2")
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

import boto3

logger = logging.getLogger(__name__)

# Duration matches the old network-resilience bespoke helper (15 min).
# Short lifetime keeps credential exposure bounded; Lambda containers are
# typically warm for <15 min between invocations anyway.
_ASSUME_ROLE_DURATION_SECONDS = 900


# ---------------------------------------------------------------------------
# Scenario 1 — static targets via env vars
# ---------------------------------------------------------------------------


def _role_env_key(alias: Optional[str]) -> str:
    if alias:
        return f"CROSS_ACCOUNT_ROLE_ARN_{alias.upper()}"
    return "CROSS_ACCOUNT_ROLE_ARN"


def _external_id_env_key(alias: Optional[str]) -> str:
    if alias:
        return f"CROSS_ACCOUNT_EXTERNAL_ID_{alias.upper()}"
    return "CROSS_ACCOUNT_EXTERNAL_ID"


@lru_cache(maxsize=8)
def _assume_role_session_cached(
    role_arn: str, external_id: str, session_name: str
) -> Optional[boto3.Session]:
    """Internal cache key: (role_arn, external_id, session_name).

    ``lru_cache`` reuses the assumed session across invocations within
    the same container, avoiding an STS call per request.
    """
    sts = boto3.client("sts")
    params: dict = {
        "RoleArn": role_arn,
        "RoleSessionName": session_name,
        "DurationSeconds": _ASSUME_ROLE_DURATION_SECONDS,
    }
    if external_id:
        params["ExternalId"] = external_id

    try:
        creds = sts.assume_role(**params)["Credentials"]
    except Exception as exc:
        logger.warning(
            "AssumeRole failed for %s (%s): %s", role_arn, session_name, exc
        )
        return None

    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


def get_cross_account_session(
    role_alias: Optional[str] = None,
) -> Optional[boto3.Session]:
    """Return an assumed-role boto3 Session, or ``None`` if nothing configured.

    Reads ``CROSS_ACCOUNT_ROLE_ARN[_<alias>]`` + optional
    ``CROSS_ACCOUNT_EXTERNAL_ID[_<alias>]`` from env. Returns ``None``
    when the role ARN is unset, which means "fall back to the Lambda
    execution role". Callers should treat ``None`` as "use a plain
    ``boto3.client``".
    """
    role_arn = os.environ.get(_role_env_key(role_alias), "")
    if not role_arn:
        return None
    external_id = os.environ.get(_external_id_env_key(role_alias), "")
    session_name = f"mcp-{role_alias.lower()}" if role_alias else "mcp-gateway-cross-account"
    return _assume_role_session_cached(role_arn, external_id, session_name)


def get_aws_client(
    service_name: str,
    region_name: Optional[str] = None,
    role_alias: Optional[str] = None,
    **kwargs,
):
    """Return a boto3 client — assumed-role if configured, else execution-role.

    Args:
        service_name: AWS service (e.g. ``"ce"``, ``"cost-optimization-hub"``).
        region_name: Optional region. Pass explicitly for us-east-1-only APIs.
        role_alias: Optional alias that selects ``CROSS_ACCOUNT_ROLE_ARN_<ALIAS>``
            from env. Omit to use the default unnamed role.
        **kwargs: Forwarded to ``boto3.client`` (endpoint_url, config, etc.).
    """
    session = get_cross_account_session(role_alias)
    client_kwargs = {"region_name": region_name} if region_name else {}
    client_kwargs.update(kwargs)

    if session:
        return session.client(service_name, **client_kwargs)
    return boto3.client(service_name, **client_kwargs)


# ---------------------------------------------------------------------------
# Scenario 2 — dynamic per-account role assumption
# ---------------------------------------------------------------------------


@lru_cache(maxsize=128)
def _assume_role_for_account_cached(
    account_id: str,
    role_name: str,
    external_id: str,
    session_name: str,
) -> Optional[boto3.Session]:
    """Cache key: (account_id, role_name, external_id, session_name).

    ``maxsize=128`` covers most Orgs; beyond that LRU evicts the oldest
    sessions, which is fine because STS is cheap when needed.
    """
    role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"
    return _assume_role_session_cached(role_arn, external_id, session_name)


def assume_role_for_account(
    account_id: str,
    role_name: str,
    service: Optional[str] = None,
    region_name: Optional[str] = None,
    external_id: str = "",
    **client_kwargs,
):
    """Dynamic per-account AssumeRole — builds the ARN from a template.

    Used for Org-wide fan-out where the same role name is provisioned in
    every spoke account (typically via a CloudFormation StackSet). The
    caller knows ``account_id`` at runtime (e.g. from
    ``TransitGatewayAttachments[].ResourceOwnerId``) and just needs a
    client that can read in that account.

    Args:
        account_id: 12-digit account ID to assume into.
        role_name: Name (not ARN) of the role to assume. The ARN is built
            as ``arn:aws:iam::{account_id}:role/{role_name}``.
        service: If set, return a client for this service instead of the
            raw session. Convenience for the common case.
        region_name: Optional region, forwarded to ``.client``.
        external_id: Optional external ID for "confused deputy" protection.
        **client_kwargs: Forwarded to ``.client`` when ``service`` is set.

    Returns:
        - ``boto3.Session`` if ``service`` is omitted.
        - A boto3 client if ``service`` is provided.
        - ``None`` on failure (swallowed by the cached STS call).
    """
    session_name = f"mcp-xacct-{account_id}"
    session = _assume_role_for_account_cached(
        account_id, role_name, external_id, session_name
    )
    if session is None:
        return None
    if service:
        kwargs = {"region_name": region_name} if region_name else {}
        kwargs.update(client_kwargs)
        return session.client(service, **kwargs)
    return session


# ---------------------------------------------------------------------------
# Test-only helper — used by unit tests to reset the lru_cache between cases.
# ---------------------------------------------------------------------------


def _reset_caches_for_testing() -> None:
    _assume_role_session_cached.cache_clear()
    _assume_role_for_account_cached.cache_clear()
