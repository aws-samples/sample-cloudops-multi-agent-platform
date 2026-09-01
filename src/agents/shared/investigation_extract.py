"""Extract compact DevOps Agent investigation references from nested traces."""

from __future__ import annotations

import json
from typing import Any

_INVESTIGATION_TOOLS = {
    "investigate_health_event",
    "investigate_operational_issue",
    "get_health_investigation",
}
_KICKOFF_TOOLS = {
    "investigate_health_event",
    "investigate_operational_issue",
}


def _bare_tool_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.split("___")[-1]


def _peel(value: Any, depth: int = 5) -> Any:
    current = value
    for _ in range(depth):
        if not isinstance(current, str):
            break
        try:
            current = json.loads(current)
        except (TypeError, ValueError):
            break
    return current


def _fallback_title(tool_name: str) -> str:
    if tool_name == "investigate_health_event":
        return "AWS Health investigation"
    return "DevOps Agent investigation"


def _reference_from_payload(tool_name: str, value: Any) -> dict | None:
    seen: set[int] = set()

    def walk(node: Any) -> dict | None:
        node = _peel(node)
        if not isinstance(node, (dict, list)):
            return None
        identity = id(node)
        if identity in seen:
            return None
        seen.add(identity)

        if isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
            return None

        investigation = (
            node["investigation"]
            if isinstance(node.get("investigation"), dict)
            else node
        )
        investigation_id = (
            investigation.get("investigationId")
            or investigation.get("investigation_id")
        )
        if isinstance(investigation_id, str) and investigation_id.strip():
            title = (
                investigation.get("requestTitle")
                or investigation.get("title")
                or _fallback_title(tool_name)
            )
            return {
                "investigationId": investigation_id.strip(),
                "title": (
                    title.strip()
                    if isinstance(title, str) and title.strip()
                    else _fallback_title(tool_name)
                ),
                "toolName": tool_name,
            }

        for child in node.values():
            found = walk(child)
            if found:
                return found
        return None

    return walk(value)


def extract_investigation_references(tool_segments: list[dict]) -> list[dict]:
    """Return de-duplicated references from nested agent ``tool_trace`` data."""
    references: dict[str, dict] = {}
    seen: set[int] = set()

    def add(reference: dict | None) -> None:
        if not reference:
            return
        investigation_id = reference["investigationId"]
        current = references.get(investigation_id)
        if (
            current is None
            or reference["toolName"] in _KICKOFF_TOOLS
        ):
            references[investigation_id] = reference

    def walk(value: Any) -> None:
        node = _peel(value)
        if not isinstance(node, (dict, list)):
            return
        identity = id(node)
        if identity in seen:
            return
        seen.add(identity)

        if isinstance(node, list):
            for item in node:
                walk(item)
            return

        name = _bare_tool_name(node.get("tool_name") or node.get("name"))
        if name in _INVESTIGATION_TOOLS:
            add(
                _reference_from_payload(
                    name,
                    node.get(
                        "output",
                        node.get("result", node.get("response", node)),
                    ),
                )
            )
        for child in node.values():
            walk(child)

    for segment in tool_segments:
        if segment.get("type") != "tool":
            continue
        try:
            tool = json.loads(segment["value"])
        except (KeyError, TypeError, ValueError):
            continue
        outer_name = _bare_tool_name(
            tool.get("name") or tool.get("tool_name")
        )
        if outer_name in _INVESTIGATION_TOOLS:
            add(_reference_from_payload(outer_name, tool))
        walk(tool)

    return sorted(
        references.values(),
        key=lambda item: item["toolName"] not in _KICKOFF_TOOLS,
    )
