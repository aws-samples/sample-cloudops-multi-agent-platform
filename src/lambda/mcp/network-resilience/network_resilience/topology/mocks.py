"""Mock topology scenarios for local dev + smoke tests.

The six scenarios mirror the source project's ``utils/mock-data.ts`` exports
byte-for-byte. Data lives in sibling ``mocks/<scenario>.json`` files, produced
by ``temp/nr-migration/scripts/dump-mocks.mjs`` (one-shot TS → JSON dumper).
Regenerate only if the source project's mock data changes; re-running the
dumper is safe and idempotent.

Canonical slugs (also the values of ``MockScenarioName`` literal in types.py):
    noResiliency, devTest, high, maximum, crossAccount, cloudWan
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from ..types import TopologyData

_MOCKS_DIR = Path(__file__).parent / "mocks"

# Map canonical slug -> filename. Filenames match the source export names
# (with ``Mock`` suffix stripped by the dumper), so ``maximum`` resolves to
# ``maximumResiliencyTopology.json``.
_SLUG_TO_FILE: Dict[str, str] = {
    "noResiliency": "noResiliencyTopology.json",
    "devTest": "devTestTopology.json",
    "high": "highResiliencyTopology.json",
    "maximum": "maximumResiliencyTopology.json",
    "crossAccount": "crossAccountTopology.json",
    "cloudWan": "cloudWanTopology.json",
}

_cache: Dict[str, TopologyData] = {}


def available_scenarios() -> list[str]:
    return list(_SLUG_TO_FILE.keys())


def load_scenario(slug: str) -> Optional[TopologyData]:
    """Return the mock TopologyData for a scenario slug, or None if unknown.

    Returned dicts are cached; callers MUST NOT mutate them (Python dicts are
    shared references). If you need a mutable copy for rule testing, call
    ``copy.deepcopy(load_scenario(...))``.
    """
    if slug not in _SLUG_TO_FILE:
        return None
    if slug in _cache:
        return _cache[slug]

    path = _MOCKS_DIR / _SLUG_TO_FILE[slug]
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        data: TopologyData = json.load(f)
    _cache[slug] = data
    return data
