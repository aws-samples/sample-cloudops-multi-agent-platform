"""Lightweight tool-call tracing for sub-agents.

Captures tool call metadata (name, input, output, duration, nested traces)
during a Strands Agent run. Since Strands does NOT fire callback_handler for
ToolResultEvent (is_callback_event=False), we use a hybrid approach:

1. The callback handler captures tool_use starts (name, input) from streaming.
2. Tool functions (like _delegate) call handler.complete_tool() directly with
   the result data after execution.

This avoids relying on the callback for completion events.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class TracingCallbackHandler:
    """Captures tool call metadata including input/output."""

    def __init__(self) -> None:
        self.tool_trace: list[dict[str, Any]] = []
        self._active: dict[str, float] = {}
        self._entries: dict[str, dict] = {}

    def __call__(self, **kwargs: Any) -> None:
        """Handle Strands callback events (streaming tool_use starts only)."""
        if "current_tool_use" in kwargs:
            tool_use = kwargs["current_tool_use"]
            tool_id = tool_use.get("toolUseId", "")
            tool_name = tool_use.get("name", "unknown")
            tool_input = tool_use.get("input", {})

            if tool_id:
                if tool_id not in self._active:
                    self._active[tool_id] = time.time()
                    entry = {
                        "tool_name": tool_name,
                        "tool_use_id": tool_id,
                        "input": tool_input,
                        "status": "started",
                    }
                    self.tool_trace.append(entry)
                    self._entries[tool_id] = entry
                elif tool_id in self._entries and tool_input:
                    # Update input as it streams in
                    self._entries[tool_id]["input"] = tool_input

    def complete_tool(
        self,
        tool_name: str,
        output: str = "",
        nested_trace: list | None = None,
        input_data: Any = None,
    ) -> None:
        """Mark a tool call as complete with its output and nested trace.

        Called directly by tool functions (e.g., _delegate) after execution,
        since Strands doesn't fire callback_handler for ToolResultEvent.
        Matches by tool_name to the most recent "started" entry.
        """
        for entry in reversed(self.tool_trace):
            if entry.get("tool_name") == tool_name and entry.get("status") == "started":
                start = self._active.pop(entry.get("tool_use_id", ""), None)
                entry["status"] = "success"
                if start is not None:
                    entry["duration_s"] = round(time.time() - start, 2)
                if output:
                    entry["output"] = output
                if nested_trace:
                    entry["tool_trace"] = nested_trace
                if input_data is not None:
                    entry["input"] = input_data
                return

    def fail_tool(self, tool_name: str, error: str = "") -> None:
        """Mark a tool call as failed."""
        for entry in reversed(self.tool_trace):
            if entry.get("tool_name") == tool_name and entry.get("status") == "started":
                start = self._active.pop(entry.get("tool_use_id", ""), None)
                entry["status"] = "error"
                if start is not None:
                    entry["duration_s"] = round(time.time() - start, 2)
                if error:
                    entry["output"] = f"Error: {error}"
                return

    def complete_tool_by_id(
        self, tool_use_id: str, output: str = "", status: str = "success"
    ) -> None:
        """Mark a tool call as complete by its toolUseId. Used for MCP tools
        where the callback handler can't capture results.

        Output is stored at full fidelity. Display-side abbreviation happens
        in the frontend (TracePanel / ReportPanel) so downstream consumers
        — the follow-up-turn LLM via AgentCore Memory, the visualizer-state
        extractor, any future data widget — all see parseable JSON.
        """
        for entry in self.tool_trace:
            if entry.get("tool_use_id") == tool_use_id:
                start = self._active.pop(tool_use_id, None)
                entry["status"] = status
                if start is not None:
                    entry["duration_s"] = round(time.time() - start, 2)
                if output:
                    entry["output"] = output
                return


def build_traced_response(response: Any, handler: TracingCallbackHandler) -> dict | str:
    """Build a response with tool call trace. Returns dict to avoid double-serialization."""
    try:
        text = response.message["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        text = str(response)

    if handler.tool_trace:
        # Mark any remaining "started" entries as "success" — these are MCP tool
        # calls where ToolResultEvent doesn't fire via callback_handler.
        for entry in handler.tool_trace:
            if entry.get("status") == "started":
                start = handler._active.pop(entry.get("tool_use_id", ""), None)
                entry["status"] = "success"
                if start is not None:
                    entry["duration_s"] = round(time.time() - start, 2)
        return {"response": text, "tool_trace": handler.tool_trace}
    return text
