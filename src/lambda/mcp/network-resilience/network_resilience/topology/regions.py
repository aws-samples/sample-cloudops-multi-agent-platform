"""Region code → friendly name lookup via AWS SSM public parameters.

Python port of source ``dx-visualizer/src/api/regions.ts``.

Only fetches long-names for the regions actually discovered in the topology,
batched 10 per GetParameters call (SSM limit). Falls back to an empty map on
permission errors.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from . import clients

logger = logging.getLogger(__name__)

_PAREN_SUFFIX = re.compile(r"\(([^)]+)\)\s*$")


def fetch_region_names(region_codes: List[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    unique = list({c for c in region_codes if c})
    if not unique:
        return result

    names = [
        f"/aws/service/global-infrastructure/regions/{code}/longName"
        for code in unique
    ]
    code_by_name: Dict[str, str] = {n: unique[i] for i, n in enumerate(names)}

    # SSM public parameters live in every region; use the caller's home region
    # so the client's region matches. We grab us-east-1 since public params
    # are present there and that's the closest equivalent to source behavior.
    # In Lambda the AWS_REGION env already matches the runtime's region.
    ssm = clients.ssm(region="us-east-1")

    try:
        for i in range(0, len(names), 10):
            batch = names[i : i + 10]
            resp = ssm.get_parameters(Names=batch)
            for p in resp.get("Parameters") or []:
                code = code_by_name.get(p.get("Name", ""))
                value = p.get("Value", "")
                if code and value:
                    result[code] = _simplify_long_name(value)
    except Exception as err:  # noqa: BLE001
        logger.warning("[AWS] SSM region-name fetch FAILED: %s", err)
        return {}

    return result


def _simplify_long_name(long_name: str) -> str:
    """``"US East (N. Virginia)"`` → ``"N. Virginia"``. Matches the source."""
    m = _PAREN_SUFFIX.search(long_name)
    return m.group(1).strip() if m else long_name
