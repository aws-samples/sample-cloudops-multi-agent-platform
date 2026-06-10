"""One-shot Haiku call that writes a short, human-friendly title back to
AgentCore Memory after a session's first assistant turn. The title renders
in the left sidebar's thread list instead of the raw first-user-prompt
preview, which is often either too long ("Run discover_dx_topology with
mock_scenario=\"cloudWan\" then assess_dx_resiliency. Show per-DXGW scores.")
or too cryptic (UUID fallback).

Design:
  * Fires inside the chat turn's `finally` block, after the assistant text
    has been saved to Memory. Non-blocking for the user-visible stream
    (the stream already flushed by the time we reach finally).
  * Idempotent: checks for an existing `<session-title>` event before
    firing. One Haiku call per thread, lifetime.
  * Best-effort: every failure path logs a warning and returns. Never
    raises. The sidebar falls back to the first-user-msg preview if no
    title event exists, so any Haiku or Memory failure is graceful.
  * Static system prompt + `cache_control` breakpoint so repeat calls in
    a 5-minute window (multiple users, multiple threads) share the cache.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

import boto3
from botocore.config import Config as BotoConfig

logger = logging.getLogger(__name__)

# Tag format: frontend scans memory events looking for this exact wrapper so
# it can prefer a generated title over the raw-prompt preview. Keep the tag
# name stable — changing it orphans every already-titled thread.
_TITLE_TAG_OPEN = "<session-title>"
_TITLE_TAG_CLOSE = "</session-title>"
_TITLE_RE = re.compile(
    re.escape(_TITLE_TAG_OPEN) + r"(.*?)" + re.escape(_TITLE_TAG_CLOSE),
    re.DOTALL,
)

_DEFAULT_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
_TITLE_MODEL_ID = os.environ.get("SESSION_TITLE_MODEL_ID", _DEFAULT_MODEL)
_TITLE_TIMEOUT_S = 6
_TITLE_MAX_TOKENS = 32
_TITLE_MAX_LEN = 64  # hard cap — UI truncates further

_SYSTEM_PROMPT = (
    "You write short, human-friendly titles for chat conversations. "
    "Given a user's first question and the assistant's answer, produce a "
    "title of 4-7 words that summarises the main topic. Focus on the "
    "subject, not the verbs (prefer 'Direct Connect resiliency review' "
    "over 'Running a DX discovery'). "
    "Respond with the title only — no quotes, no trailing punctuation, no "
    "markdown, no explanation."
)

_bedrock_client = None
_memory_client = None


def _get_bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        region = os.environ.get("AWS_REGION", "us-east-1")
        cfg = BotoConfig(
            read_timeout=_TITLE_TIMEOUT_S,
            connect_timeout=2,
            retries={"max_attempts": 1, "mode": "standard"},
        )
        _bedrock_client = boto3.client("bedrock-runtime", region_name=region, config=cfg)
    return _bedrock_client


def _get_memory(region: str):
    global _memory_client
    if _memory_client is None:
        _memory_client = boto3.client("bedrock-agentcore", region_name=region)
    return _memory_client


def _has_existing_title(
    memory_id: str, session_id: str, actor_id: str, region: str
) -> bool:
    """Return True if this session already has a `<session-title>` event."""
    try:
        client = _get_memory(region)
        # Small page — title lives on the first ASSISTANT event, which
        # would be at the start of the session's event list.
        resp = client.list_events(
            memoryId=memory_id,
            actorId=actor_id,
            sessionId=session_id,
            includePayloads=True,
            maxResults=20,
        )
        for event in resp.get("events", []):
            for item in event.get("payload", []):
                conv = item.get("conversational", {})
                text = (conv.get("content") or {}).get("text", "") or ""
                if _TITLE_TAG_OPEN in text:
                    return True
        return False
    except Exception as exc:
        logger.warning("session-title: existence check failed: %s", exc)
        # Fail-safe toward NOT generating — otherwise we could double-write on
        # a transient list_events glitch. The sidebar falls back gracefully.
        return True


def _invoke_haiku(user_prompt: str, assistant_text: str) -> str | None:
    """Call Haiku with the static system prompt + compact user payload. Returns
    the title string, or None on any failure."""
    # Keep the user side compact; ship only as much context as the model needs.
    payload = {
        "userPrompt": user_prompt[:400],
        "assistantExcerpt": assistant_text[:600],
    }

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": _TITLE_MAX_TOKENS,
        "temperature": 0,
        "system": [
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(payload, separators=(",", ":")),
                    }
                ],
            }
        ],
    }

    try:
        client = _get_bedrock()
        resp = client.invoke_model(modelId=_TITLE_MODEL_ID, body=json.dumps(body))
        parsed = json.loads(resp["body"].read())
    except Exception as exc:
        logger.warning("session-title: Haiku invoke failed (%s): %s", type(exc).__name__, exc)
        return None

    try:
        text = "".join(
            block.get("text", "")
            for block in parsed.get("content", [])
            if block.get("type") == "text"
        )
    except Exception as exc:
        logger.warning("session-title: response shape unexpected: %s", exc)
        return None

    # Post-processing: Haiku occasionally wraps in quotes or tacks on a trailing
    # period despite the prompt. Strip both.
    title = text.strip().strip('"').strip("'").rstrip(".").strip()
    if not title:
        return None
    # Hard cap — the sidebar truncates further but we shouldn't persist a
    # runaway 200-char sentence.
    if len(title) > _TITLE_MAX_LEN:
        title = title[: _TITLE_MAX_LEN].rstrip()
    return title


def _save_title_event(
    title: str, memory_id: str, session_id: str, actor_id: str, region: str
) -> None:
    """Write a tagged title event to Memory. Uses an ASSISTANT role because
    AgentCore Memory's Conversational schema requires USER/ASSISTANT/TOOL;
    the tag wrapper keeps it from leaking into agent context (sidebar reads
    it via string match, memory replay strips it out in load_history)."""
    try:
        client = _get_memory(region)
        client.create_event(
            memoryId=memory_id,
            actorId=actor_id,
            sessionId=session_id,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[
                {
                    "conversational": {
                        "role": "ASSISTANT",
                        "content": {
                            "text": f"{_TITLE_TAG_OPEN}{title}{_TITLE_TAG_CLOSE}"
                        },
                    }
                }
            ],
        )
        logger.info("session-title: saved '%s' for session %s", title, session_id[:20])
    except Exception as exc:
        logger.warning("session-title: save failed: %s", exc)


def maybe_generate_and_save_title(
    *,
    memory_id: str,
    session_id: str,
    actor_id: str,
    user_prompt: str,
    assistant_text: str,
    region: str,
) -> None:
    """Entry point — called from the chat turn's `finally` block.

    Guards:
      * All required fields present.
      * No existing `<session-title>` event for this session.
      * Non-empty assistant text (skip on error turns where nothing was saved).

    Never raises. Never blocks the user — intended to run after stream close.
    """
    if not (memory_id and session_id and actor_id):
        return
    if not assistant_text or not assistant_text.strip():
        return
    if _has_existing_title(memory_id, session_id, actor_id, region):
        return
    title = _invoke_haiku(user_prompt, assistant_text)
    if not title:
        return
    _save_title_event(title, memory_id, session_id, actor_id, region)


def extract_title_from_events(events: list) -> str | None:
    """Scan an events list (as returned by bedrock-agentcore list_events)
    for a `<session-title>` tag. Returns the title or None.

    Used by the REST `_list_sessions` handler so it can prefer a generated
    title over the first-user-message preview.
    """
    for event in events or []:
        for item in event.get("payload", []):
            conv = item.get("conversational", {})
            text = (conv.get("content") or {}).get("text", "") or ""
            m = _TITLE_RE.search(text)
            if m:
                title = m.group(1).strip()
                if title:
                    return title
    return None
