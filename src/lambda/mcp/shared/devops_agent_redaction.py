"""Recursive secret redaction for DevOps Agent response boundaries."""

from __future__ import annotations

import json
import re
from typing import Any

_SECRET_KEYS = re.compile(
    r"(secret|password|passwd|token|authorization|credential|private.?key|"
    r"access.?key|session.?key|api.?key)",
    re.IGNORECASE,
)
_SECRET_VALUES = (
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", re.IGNORECASE),
)


def redact(value: Any, key: str = "") -> Any:
    """Recursively redact response secrets without changing at-rest records."""
    if _SECRET_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {name: redact(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            decoded = None
        if isinstance(decoded, (dict, list)):
            value = json.dumps(redact(decoded), separators=(",", ":"), default=str)
        redacted = value
        for pattern in _SECRET_VALUES:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted
    return value
