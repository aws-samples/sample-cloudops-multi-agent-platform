"""Bedrock Guardrail pre-flight check on user input.

Calls the standalone ApplyGuardrail API to evaluate ONLY the raw user message
before it enters the agent pipeline. This avoids the false-positive problem
where model-level guardrails flagged the platform's own system prompts as
injection attempts. The system prompt is assembled separately (in
``agent_base.py``) and attached only AFTER this check passes — the classifier
never sees it, and it is not user-modifiable (baked into the container image
from ``hierarchy.json``).

Two operating modes, selected by the ``GUARDRAIL_MODE`` env var:
- ``block`` (default): a guardrail intervention raises ``GuardrailBlocked`` and
  the request is refused before it reaches the model.
- ``detect``: interventions are logged but the request is allowed through. Use
  this to observe what the guardrail WOULD block without impacting traffic —
  the reviewer-recommended "start in detect mode" posture. Defense still rests
  on ``_NO_FABRICATION_PREAMBLE`` + per-tool IAM scoping in this mode.

Either way only the raw user message is screened — never system prompts,
agent instructions, or conversation history.

Environment variables:
    BEDROCK_GUARDRAIL_ID: Guardrail identifier (empty = disabled)
    BEDROCK_GUARDRAIL_VERSION: Version number (e.g., "1" or "DRAFT")
    GUARDRAIL_MODE: "block" (default) or "detect" (log-only, non-blocking)
"""

from __future__ import annotations

import logging
import os

import boto3

logger = logging.getLogger(__name__)

_client = None


def _get_guardrail_id() -> str:
    return os.environ.get("BEDROCK_GUARDRAIL_ID", "")


def _get_guardrail_version() -> str:
    return os.environ.get("BEDROCK_GUARDRAIL_VERSION", "")


def _get_mode() -> str:
    """Return the guardrail mode: 'detect' (log-only) or 'block' (default)."""
    mode = os.environ.get("GUARDRAIL_MODE", "block").strip().lower()
    return "detect" if mode == "detect" else "block"


def _get_client():
    global _client
    if _client is None:
        region = os.environ.get("AWS_REGION", "us-east-1")
        _client = boto3.client("bedrock-runtime", region_name=region)
    return _client


class GuardrailBlocked(Exception):
    """Raised when the guardrail blocks user input."""

    def __init__(self, message: str, action: str = "BLOCKED"):
        self.message = message
        self.action = action
        super().__init__(message)


def check_user_input(user_prompt: str) -> dict | None:
    """Evaluate user input against the Bedrock Guardrail.

    Returns None if the guardrail is disabled or the input passes clean.
    In ``block`` mode (default), raises GuardrailBlocked when the guardrail
    intervenes. In ``detect`` mode, logs the intervention and returns the
    assessment dict WITHOUT raising — the request is allowed through.
    Also returns the assessment dict for any non-blocking finding.

    Only the raw user message is sent — no system prompts, no agent
    instructions, no conversation history. This eliminates false positives
    on multi-agent delegation patterns.
    """
    guardrail_id = _get_guardrail_id()
    guardrail_version = _get_guardrail_version()

    if not guardrail_id or not guardrail_version:
        return None

    if not user_prompt or not user_prompt.strip():
        return None

    mode = _get_mode()

    try:
        client = _get_client()
        response = client.apply_guardrail(
            guardrailIdentifier=guardrail_id,
            guardrailVersion=guardrail_version,
            source="INPUT",
            content=[{"text": {"text": user_prompt}}],
        )

        action = response.get("action", "NONE")
        assessments = response.get("assessments", [])

        if action == "GUARDRAIL_INTERVENED":
            outputs = response.get("outputs", [])
            blocked_message = outputs[0]["text"] if outputs else (
                "Your request was blocked by the content safety filter."
            )
            if mode == "detect":
                # Detect mode: log what WOULD have been blocked, but let the
                # request proceed. Lets operators observe injection attempts
                # without impacting traffic before committing to block mode.
                logger.warning(
                    "Guardrail INTERVENED (detect mode — NOT blocking): "
                    "assessments=%s",
                    assessments,
                )
                return {"action": action, "assessments": assessments, "mode": "detect"}
            logger.warning(
                "Guardrail BLOCKED input: action=%s assessments=%s",
                action,
                assessments,
            )
            raise GuardrailBlocked(blocked_message, action=action)

        if assessments:
            logger.info(
                "Guardrail assessment (non-blocking): action=%s assessments=%s",
                action,
                assessments,
            )
            return {"action": action, "assessments": assessments}

        return None

    except GuardrailBlocked:
        raise
    except Exception:
        logger.exception("Guardrail check failed (non-fatal, allowing request)")
        return None
