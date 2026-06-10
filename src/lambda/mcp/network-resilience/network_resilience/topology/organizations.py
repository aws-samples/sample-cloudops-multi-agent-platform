"""Organizations + IAM account helpers and AssumeRole.

Python port of source ``dx-visualizer/src/api/organizations.ts``.

In Lambda the default credentials chain (execution role) applies. For spoke
account enrichment (Phase 5 of the fetch orchestrator), we AssumeRole into
each spoke and return a transient boto3 Session that child fetchers use.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import boto3

from . import clients

logger = logging.getLogger(__name__)


def list_org_accounts() -> List[Dict[str, str]]:
    """List all ACTIVE accounts in the Organization. Requires
    ``organizations:ListAccounts``.
    """
    org = clients.organizations()
    accounts: List[Dict[str, str]] = []
    next_token: str | None = None
    while True:
        kwargs: Dict[str, Any] = {}
        if next_token:
            kwargs["NextToken"] = next_token
        try:
            res = org.list_accounts(**kwargs)
        except Exception as err:  # noqa: BLE001
            logger.warning("[AWS] list_accounts failed: %s", err)
            return accounts
        for a in res.get("Accounts") or []:
            if a.get("Status") == "ACTIVE" and a.get("Id"):
                accounts.append(
                    {
                        "accountId": a["Id"],
                        "accountName": a.get("Name") or a["Id"],
                        "status": a["Status"],
                    }
                )
        next_token = res.get("NextToken")
        if not next_token:
            break
    return accounts


def resolve_account_name(account_id: str) -> Optional[str]:
    """Try Organizations DescribeAccount first, fall back to IAM account
    alias, else None. Matches source ordering.
    """
    try:
        org = clients.organizations()
        res = org.describe_account(AccountId=account_id)
        name = (res.get("Account") or {}).get("Name")
        if name:
            return name
    except Exception:  # noqa: BLE001 — no org permissions, fall through
        pass

    try:
        iam = clients.iam()
        res = iam.list_account_aliases()
        aliases = res.get("AccountAliases") or []
        if aliases:
            return aliases[0]
    except Exception:  # noqa: BLE001 — no iam:ListAccountAliases
        pass

    return None


def assume_role_session(
    target_account_id: str, role_name: str = "NetworkReadOnlyRole"
) -> Optional[boto3.Session]:
    """AssumeRole into a target account and return a boto3 Session.

    Thin wrapper around the platform-shared ``assume_role_for_account``
    helper. Historic module-level name is kept so existing callers
    (``fetch.py:enrich_cross_account``) don't need to change. Sessions
    still have a 15-minute lifetime and are cached per
    ``(account_id, role_name)`` inside the shared module.

    Returns ``None`` on failure so the caller can skip the spoke and
    append a ``fetchErrors`` entry without aborting the whole discovery.
    """
    # Import here rather than at module top because this package is also
    # reachable from unit tests that stub out boto3 entirely — deferring
    # keeps import ordering predictable.
    from shared.cross_account import assume_role_for_account

    return assume_role_for_account(target_account_id, role_name)
