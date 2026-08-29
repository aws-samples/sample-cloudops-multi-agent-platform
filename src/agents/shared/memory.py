"""Manual memory management for frontend-facing agents.

Provides load/save functions for conversation history using the AgentCore
Memory API (create_event/list_events). Does NOT use AgentCoreMemorySessionManager
— see project conventions for why.

Usage::

    from agents.shared.memory import load_history, save_user_message, save_assistant_message

    history = load_history(memory_id, session_id, actor_id, region)
    save_user_message(memory_id, session_id, actor_id, prompt, region)
    save_assistant_message(memory_id, session_id, actor_id, enriched_text, region)
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

logger = logging.getLogger(__name__)

_memory_client = None
_dynamodb_client = None
_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()
_INVESTIGATIONS_TABLE_NAME = os.environ.get("INVESTIGATIONS_TABLE_NAME", "")
_INVESTIGATION_REF_RE = re.compile(
    r"\n?<investigation-ref>(.*?)</investigation-ref>",
    re.DOTALL,
)

# AgentCore Memory rejects any single conversational event whose text exceeds
# 100,000 chars (CreateEvent ValidationException). A large report follow-up —
# which quotes the report's tables plus full <tool> outputs — can blow past
# this; the event was then dropped entirely and the turn vanished on reload.
# Keep a margin under the hard cap for the role/JSON envelope overhead.
_MAX_EVENT_TEXT = 96_000
_OMITTED_TOOL_OUTPUT = "[tool output omitted from chat memory]"


def _compact_tool_block(block: str) -> str:
    """Keep trace names and inputs while dropping oversized result payloads."""
    try:
        inner = block[len("<tool>") : -len("</tool>")]
        parsed = json.loads(inner)
    except (TypeError, ValueError):
        return '<tool>{"truncated":true}</tool>'

    def compact(node: Any) -> Any:
        if isinstance(node, list):
            return [compact(item) for item in node]
        if not isinstance(node, dict):
            return node
        output: dict[str, Any] = {}
        for key, value in node.items():
            if key == "output":
                output[key] = _OMITTED_TOOL_OUTPUT
            elif key == "tool_trace":
                output[key] = compact(value)
            elif key in {
                "name",
                "tool_name",
                "tool_use_id",
                "input",
                "status",
                "duration_s",
            }:
                output[key] = value
        return output

    compacted = json.dumps(compact(parsed), separators=(",", ":"))
    return f"<tool>{compacted}</tool>"

# Structured UI the frontend rehydrates on reload is persisted in its OWN
# compact tags (<visualizer-state>, <report-body>, <artifact>, <report-pending>,
# <suggestions>), which the trimmer must NEVER touch — the card/report is
# reconstructed from them regardless of how the bulky raw <tool> trace is
# trimmed. <tool> blocks are pure trace verbosity for reload purposes (the
# frontend re-abbreviates them), so they are the only thing sacrificed here.
def _fit_event_text(text: str) -> str:
    """Shrink *text* to fit the Memory per-event limit without losing UI state.

    The compact <visualizer-state>/<report-body>/<artifact>/<suggestions> tags
    carry everything the UI needs on reload, so they are protected. Only whole
    <tool>…</tool> trace blocks are trimmed (biggest first, so tag structure is
    never left half-open), then a hard tail-cut as an absolute last resort.
    """
    if len(text) <= _MAX_EVENT_TEXT:
        return text

    tool_re = re.compile(r"<tool>[\s\S]*?</tool>", re.DOTALL)
    placeholder = '<tool>{"truncated":true}</tool>'

    while len(text) > _MAX_EVENT_TEXT:
        blocks = [
            match
            for match in tool_re.finditer(text)
            if match.group(0) != placeholder
        ]
        if not blocks:
            break
        match = max(blocks, key=lambda item: item.end() - item.start())
        replacement = _compact_tool_block(match.group(0))
        if len(replacement) >= len(match.group(0)):
            replacement = placeholder
        text = text[: match.start()] + replacement + text[match.end() :]

    # Absolute last resort — no <tool> blocks left and still over (e.g. a giant
    # report body). Hard-cut the head, but keep compact UI state so cards still
    # rehydrate even when their raw tool traces are gone.
    if len(text) > _MAX_EVENT_TEXT:
        protected_re = re.compile(
            r"<(?:visualizer-state|investigation-ref)>[\s\S]*?</"
            r"(?:visualizer-state|investigation-ref)>",
            re.DOTALL,
        )
        protected = []
        for match in protected_re.finditer(text):
            if match.group(0) not in protected:
                protected.append(match.group(0))
        body = protected_re.sub("", text)
        suffix = "\n".join(protected)
        marker = "\n[…truncated for storage…]\n"
        if suffix and len(suffix) + len(marker) < _MAX_EVENT_TEXT:
            head_budget = _MAX_EVENT_TEXT - len(suffix) - len(marker)
            text = body[:head_budget] + marker + suffix
        else:
            text = text[: _MAX_EVENT_TEXT - 40] + "\n[…truncated for storage…]"
    return text


def _get_client(region: str = "us-east-1"):
    global _memory_client
    if _memory_client is None:
        _memory_client = boto3.client("bedrock-agentcore", region_name=region)
    return _memory_client


def _get_dynamodb_client(region: str = "us-east-1"):
    global _dynamodb_client
    if _dynamodb_client is None:
        _dynamodb_client = boto3.client("dynamodb", region_name=region)
    return _dynamodb_client


def _investigation_id(raw_reference: str) -> str:
    try:
        reference = json.loads(raw_reference)
    except (TypeError, ValueError):
        return ""
    investigation_id = reference.get("investigationId")
    return investigation_id.strip() if isinstance(investigation_id, str) else ""


def _compact_investigation_context(meta: dict[str, Any]) -> str:
    """Return the persisted investigation projection intended for model context."""

    def item(value: Any) -> dict[str, str] | None:
        if not isinstance(value, dict):
            return None
        compact = {
            key: str(value[key]).strip()[:limit]
            for key, limit in (("title", 240), ("description", 3000))
            if value.get(key)
        }
        return compact or None

    outcome = meta.get("outcome")
    outcome = outcome if isinstance(outcome, dict) else {}
    mitigation = outcome.get("mitigation")
    compact_mitigation = None
    if isinstance(mitigation, dict):
        compact_mitigation = {
            key: value
            for key, value in {
                "status": str(mitigation.get("status", "")).strip()[:80],
                "terminal": bool(mitigation.get("terminal", False)),
                "action": str(mitigation.get("action", "")).strip()[:1000],
                "description": str(mitigation.get("description", "")).strip()[:3000],
            }.items()
            if value not in ("", None)
        }

    context = {
        "investigationId": str(meta.get("investigationId", "")),
        "title": str(meta.get("requestTitle", "DevOps Agent investigation"))[:400],
        "workflowState": str(meta.get("workflowState", "")),
        "providerStatus": str(meta.get("providerStatus", "")),
        "incident": item(outcome.get("incident")),
        "rootCause": item(outcome.get("rootCause")),
        "mitigation": compact_mitigation,
    }
    compact = {key: value for key, value in context.items() if value is not None}
    return (
        "<investigation-context>"
        + json.dumps(compact, separators=(",", ":"), ensure_ascii=True)
        + "</investigation-context>"
    )


def _load_investigations(
    investigation_ids: set[str], region: str
) -> dict[str, dict[str, Any]]:
    if not investigation_ids or not _INVESTIGATIONS_TABLE_NAME:
        return {}
    client = _get_dynamodb_client(region)
    found: dict[str, dict[str, Any]] = {}
    ids = sorted(investigation_ids)
    for start in range(0, len(ids), 100):
        request = {
            _INVESTIGATIONS_TABLE_NAME: {
                "Keys": [
                    {
                        "PK": _SERIALIZER.serialize(f"INV#{investigation_id}"),
                        "SK": _SERIALIZER.serialize("META"),
                    }
                    for investigation_id in ids[start : start + 100]
                ],
                "ProjectionExpression": (
                    "investigationId, requestTitle, workflowState, providerStatus, "
                    "outcome"
                ),
                "ConsistentRead": True,
            }
        }
        for _ in range(3):
            response = client.batch_get_item(RequestItems=request)
            for raw in response.get("Responses", {}).get(
                _INVESTIGATIONS_TABLE_NAME, []
            ):
                meta = {
                    key: _DESERIALIZER.deserialize(value)
                    for key, value in raw.items()
                }
                investigation_id = str(meta.get("investigationId", ""))
                if investigation_id:
                    found[investigation_id] = meta
            unprocessed = response.get("UnprocessedKeys", {}).get(
                _INVESTIGATIONS_TABLE_NAME
            )
            if not unprocessed:
                break
            request = {_INVESTIGATIONS_TABLE_NAME: unprocessed}
    return found


def _hydrate_investigation_contexts(
    messages: list[dict], region: str
) -> list[dict]:
    """Replace UI references with the latest compact, authoritative projection."""
    references: list[tuple[int, str]] = []
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        text = message["content"][0]["text"]
        for match in _INVESTIGATION_REF_RE.finditer(text):
            investigation_id = _investigation_id(match.group(1))
            if investigation_id:
                references.append((index, investigation_id))
    if not references or not _INVESTIGATIONS_TABLE_NAME:
        return messages

    try:
        investigations = _load_investigations(
            {investigation_id for _, investigation_id in references}, region
        )
    except Exception as exc:
        logger.warning("Failed to hydrate investigation context: %s", exc)
        return messages

    last_message = {
        investigation_id: index for index, investigation_id in references
    }
    emitted: set[str] = set()
    hydrated = []
    for index, message in enumerate(messages):
        text = message["content"][0]["text"]
        if message.get("role") != "assistant":
            hydrated.append(message)
            continue

        def replace(match: re.Match) -> str:
            investigation_id = _investigation_id(match.group(1))
            meta = investigations.get(investigation_id)
            if not meta:
                return match.group(0)
            if index != last_message[investigation_id] or investigation_id in emitted:
                return ""
            emitted.add(investigation_id)
            return "\n" + _compact_investigation_context(meta)

        text = _INVESTIGATION_REF_RE.sub(replace, text).strip()
        if text:
            hydrated.append({**message, "content": [{"text": text}]})
    return hydrated


def load_history(
    memory_id: str, session_id: str, actor_id: str, region: str = "us-east-1"
) -> list[dict]:
    """Load conversation history and convert to Strands message format."""
    if not memory_id or not session_id or not actor_id:
        return []
    try:
        client = _get_client(region)
        all_events: list[dict] = []
        next_token = None
        while True:
            params: dict[str, Any] = {
                "memoryId": memory_id,
                "actorId": actor_id,
                "sessionId": session_id,
                "includePayloads": True,
                "maxResults": 100,
            }
            if next_token:
                params["nextToken"] = next_token
            resp = client.list_events(**params)
            all_events.extend(resp.get("events", []))
            next_token = resp.get("nextToken")
            if not next_token:
                break

        all_events.reverse()
        messages = []
        for event in all_events:
            for item in event.get("payload", []):
                conv = item.get("conversational", {})
                if not conv:
                    continue
                role_raw = conv.get("role", "")
                content = conv.get("content", {})
                text = (
                    content.get("text", "")
                    if isinstance(content, dict)
                    else str(content)
                )
                if not text.strip():
                    continue
                # Strip frontend-only display tags (report metadata cards,
                # next-turn suggestion chips, sidebar session-title markers).
                # `<tool>` tags are deliberately preserved — they record what
                # the agent did in prior turns (inputs, outputs, mock-scenario
                # markers) and let the model resolve references like "the
                # diagram above" or "that report" without claiming nothing is
                # in its history. See the vocabulary/evidence clause in
                # `_NO_FABRICATION_PREAMBLE`.
                text = re.sub(r"\n?<artifact>.*?</artifact>", "", text, flags=re.DOTALL)
                text = re.sub(
                    r"\n?<suggestions>.*?</suggestions>", "", text, flags=re.DOTALL
                )
                text = re.sub(
                    r"\n?<session-title>.*?</session-title>", "", text, flags=re.DOTALL
                )
                # Frontend-only VisualizerCard payload — the compact topology
                # JSON is for the browser to rehydrate the card, NOT for the
                # model. Stripping it keeps 30-80KB of topology out of every
                # follow-up turn's context (the <tool> trace above already
                # gives the model what it needs to reference "the diagram").
                text = re.sub(
                    r"\n?<visualizer-state>.*?</visualizer-state>", "", text, flags=re.DOTALL
                )
                # Frontend-only report-mode marker on user turns — the model
                # must never see it (it's not content, just a UI badge signal).
                text = re.sub(r"\n?<report-request/>", "", text)
                text = text.strip()
                if not text:
                    continue
                role = "assistant" if role_raw == "ASSISTANT" else "user"
                messages.append({"role": role, "content": [{"text": text}]})

        messages = _hydrate_investigation_contexts(messages, region)
        logger.info(
            "Loaded %d history messages for session %s", len(messages), session_id[:20]
        )
        return messages
    except Exception as exc:
        if "not found" not in str(exc).lower():
            logger.warning("Failed to load history: %s", exc)
        return []


def save_user_message(
    memory_id: str,
    session_id: str,
    actor_id: str,
    prompt: str,
    region: str = "us-east-1",
    is_report: bool = False,
) -> None:
    """Save user message to Memory immediately (before streaming starts).

    When ``is_report`` is True, a ``<report-request/>`` sentinel is appended to
    the saved text. It is a FRONTEND-ONLY marker (like ``<artifact>`` etc.):
    ``load_history`` strips it before the model ever sees it, and the browser
    REST path (core-api ``_get_session_history``) turns it into an ``is_report``
    flag it strips from the content, so the UI can badge the user's prompt
    bubble as a report request — and that badge survives a reload.
    """
    if not memory_id or not session_id or not actor_id:
        return
    try:
        text = prompt + "\n<report-request/>" if is_report else prompt
        text = _fit_event_text(text)
        client = _get_client(region)
        client.create_event(
            memoryId=memory_id,
            actorId=actor_id,
            sessionId=session_id,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[{"conversational": {"content": {"text": text}, "role": "USER"}}],
        )
    except Exception as exc:
        logger.warning("Failed to save user message: %s", exc)


def save_assistant_message(
    memory_id: str,
    session_id: str,
    actor_id: str,
    enriched_text: str,
    region: str = "us-east-1",
) -> None:
    """Save enriched assistant response to Memory (after streaming completes)."""
    if not memory_id or not session_id or not actor_id or not enriched_text.strip():
        return
    text = _fit_event_text(enriched_text)
    if len(text) < len(enriched_text):
        logger.info(
            "Trimmed assistant message %d -> %d chars to fit Memory event limit",
            len(enriched_text), len(text),
        )
    try:
        client = _get_client(region)
        client.create_event(
            memoryId=memory_id,
            actorId=actor_id,
            sessionId=session_id,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[
                {
                    "conversational": {
                        "content": {"text": text},
                        "role": "ASSISTANT",
                    }
                }
            ],
        )
        logger.info("Saved conversation to Memory for session %s", session_id[:20])
    except Exception as exc:
        logger.warning("Failed to save assistant message: %s", exc)


def build_enriched_text(ordered_segments: list[dict]) -> str:
    """Build enriched assistant text with <tool>, <think>, <suggestions> tags."""
    from agents.shared.redact import redact

    enriched_parts: list[str] = []
    text_buffer: list[str] = []
    thinking_buffer: list[str] = []

    def flush_text():
        nonlocal text_buffer
        if text_buffer:
            enriched_parts.append("".join(text_buffer))
            text_buffer = []

    def flush_thinking():
        nonlocal thinking_buffer
        if thinking_buffer:
            enriched_parts.append(f'<think>{"".join(thinking_buffer)}</think>')
            thinking_buffer = []

    for seg in ordered_segments:
        seg_type = seg.get("type", "")
        if seg_type == "text":
            flush_thinking()
            text_buffer.append(seg["value"])
        elif seg_type == "thinking":
            flush_text()
            thinking_buffer.append(seg["value"])
        elif seg_type == "tool":
            flush_text()
            flush_thinking()
            enriched_parts.append(f'<tool>{seg["value"]}</tool>')
        elif seg_type == "suggestions":
            flush_text()
            flush_thinking()
            enriched_parts.append(f'<suggestions>{seg["value"]}</suggestions>')
        elif seg_type == "visualizer_state":
            # Compact DX topology for reload-time VisualizerCard rehydration.
            # page.tsx reads this <visualizer-state> tag directly, so the card
            # survives even if the bulky raw <tool> topology gets trimmed.
            flush_text()
            flush_thinking()
            enriched_parts.append(f'<visualizer-state>{seg["value"]}</visualizer-state>')
        elif seg_type == "investigation_ref":
            flush_text()
            flush_thinking()
            enriched_parts.append(
                f'<investigation-ref>{seg["value"]}</investigation-ref>'
            )

    flush_text()
    flush_thinking()
    return redact("\n".join(enriched_parts))
