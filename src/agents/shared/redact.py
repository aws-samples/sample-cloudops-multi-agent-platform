"""Output redaction for sensitive AWS patterns.

Strips AWS account IDs, IAM ARNs, role session names, and external IDs
from LLM-generated text before it reaches the frontend or gets persisted
to memory/session history. Patterns are replaced with safe placeholders
so the response remains coherent without leaking infrastructure details.
"""

import re

_AWS_ACCOUNT_ID = re.compile(r"\b\d{12}\b")

_IAM_ARN = re.compile(
    r"arn:aws[a-z\-]*:[a-z0-9\-]+:[a-z0-9\-]*:\d{12}:[a-zA-Z0-9\-_/:.+=@]+"
)

_ROLE_SESSION = re.compile(
    r"(?:RoleSessionName|roleSessionName|role_session_name)\s*[=:]\s*['\"]?[A-Za-z0-9_\-.]+"
)

_EXTERNAL_ID = re.compile(
    r"(?:ExternalId|externalId|external_id)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]+"
)

_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")

_PLACEHOLDER = "[REDACTED]"


def redact(text: str) -> str:
    """Remove sensitive AWS patterns from text, returning sanitized version."""
    if not text:
        return text
    text = _IAM_ARN.sub(_PLACEHOLDER, text)
    text = _ACCESS_KEY.sub(_PLACEHOLDER, text)
    text = _EXTERNAL_ID.sub(_PLACEHOLDER, text)
    text = _ROLE_SESSION.sub(_PLACEHOLDER, text)
    text = _AWS_ACCOUNT_ID.sub(_PLACEHOLDER, text)
    return text
