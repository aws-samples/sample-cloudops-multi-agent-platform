"""Unit tests for agents.shared.tracing — tool call tracing."""

import time
from unittest.mock import MagicMock

from agents.shared.tracing import (
    TracingCallbackHandler,
    build_traced_response,
)


class TestTracingCallbackHandler:
    def test_captures_tool_start(self):
        handler = TracingCallbackHandler()
        handler(current_tool_use={"toolUseId": "t1", "name": "my_tool", "input": {}})
        assert len(handler.tool_trace) == 1
        assert handler.tool_trace[0]["tool_name"] == "my_tool"
        assert handler.tool_trace[0]["status"] == "started"

    def test_updates_input_on_subsequent_callbacks(self):
        handler = TracingCallbackHandler()
        handler(current_tool_use={"toolUseId": "t1", "name": "my_tool", "input": {}})
        handler(
            current_tool_use={
                "toolUseId": "t1",
                "name": "my_tool",
                "input": {"key": "val"},
            }
        )
        assert handler.tool_trace[0]["input"] == {"key": "val"}

    def test_complete_tool_by_name(self):
        handler = TracingCallbackHandler()
        handler(current_tool_use={"toolUseId": "t1", "name": "agent_a", "input": {}})
        handler.complete_tool(
            "agent_a", output="result", nested_trace=[{"tool_name": "sub"}]
        )
        assert handler.tool_trace[0]["status"] == "success"
        assert handler.tool_trace[0]["output"] == "result"
        assert handler.tool_trace[0]["tool_trace"] == [{"tool_name": "sub"}]
        assert "duration_s" in handler.tool_trace[0]

    def test_complete_tool_by_id(self):
        handler = TracingCallbackHandler()
        handler(current_tool_use={"toolUseId": "t1", "name": "tool_x", "input": {}})
        handler.complete_tool_by_id("t1", output="done", status="success")
        assert handler.tool_trace[0]["status"] == "success"
        assert handler.tool_trace[0]["output"] == "done"

    def test_fail_tool(self):
        handler = TracingCallbackHandler()
        handler(current_tool_use={"toolUseId": "t1", "name": "bad_tool", "input": {}})
        handler.fail_tool("bad_tool", error="timeout")
        assert handler.tool_trace[0]["status"] == "error"
        assert "timeout" in handler.tool_trace[0]["output"]

    def test_duration_tracked(self):
        handler = TracingCallbackHandler()
        handler(current_tool_use={"toolUseId": "t1", "name": "slow", "input": {}})
        time.sleep(0.05)
        handler.complete_tool("slow", output="done")
        assert handler.tool_trace[0]["duration_s"] >= 0.04

    def test_multiple_tools(self):
        handler = TracingCallbackHandler()
        handler(current_tool_use={"toolUseId": "t1", "name": "a", "input": {}})
        handler(current_tool_use={"toolUseId": "t2", "name": "b", "input": {}})
        handler.complete_tool("a", output="r1")
        handler.complete_tool("b", output="r2")
        assert len(handler.tool_trace) == 2
        assert handler.tool_trace[0]["output"] == "r1"
        assert handler.tool_trace[1]["output"] == "r2"


class TestBuildTracedResponse:
    def test_returns_text_when_no_traces(self):
        response = MagicMock()
        response.message = {"content": [{"text": "Hello"}]}
        handler = TracingCallbackHandler()
        result = build_traced_response(response, handler)
        assert result == "Hello"

    def test_returns_dict_with_traces(self):
        response = MagicMock()
        response.message = {"content": [{"text": "Answer"}]}
        handler = TracingCallbackHandler()
        handler(current_tool_use={"toolUseId": "t1", "name": "tool_a", "input": {}})
        handler.complete_tool("tool_a", output="data")
        result = build_traced_response(response, handler)
        assert isinstance(result, dict)
        assert result["response"] == "Answer"
        assert len(result["tool_trace"]) == 1

    def test_marks_started_entries_as_success(self):
        response = MagicMock()
        response.message = {"content": [{"text": "ok"}]}
        handler = TracingCallbackHandler()
        handler(current_tool_use={"toolUseId": "t1", "name": "mcp_tool", "input": {}})
        # Don't call complete_tool — simulates MCP tools where callback doesn't fire
        result = build_traced_response(response, handler)
        assert result["tool_trace"][0]["status"] == "success"


class TestFullFidelityOutput:
    """Large structured outputs must round-trip without truncation so
    follow-up-turn LLMs and frontend extractors can re-parse them."""

    def test_large_json_output_round_trips(self):
        import json

        handler = TracingCallbackHandler()
        handler(current_tool_use={"toolUseId": "t1", "name": "assess", "input": {}})
        # Simulate a ~20KB structured assessment payload.
        large_payload = {
            "status": "success",
            "data": {
                "perDxGateway": [
                    {
                        "dxGatewayId": f"dxgw-{i}",
                        "score": i * 10,
                        "recommendations": [
                            {"id": f"r{i}-{j}", "description": "x" * 200}
                            for j in range(5)
                        ],
                    }
                    for i in range(8)
                ]
            },
        }
        serialized = json.dumps(large_payload)
        # Sanity: well beyond the old 3000-char hard cap in _smart_truncate.
        assert len(serialized) > 8_000

        handler.complete_tool_by_id("t1", output=serialized, status="success")
        entry = handler.tool_trace[0]
        assert entry["output"] == serialized
        parsed = json.loads(entry["output"])
        assert len(parsed["data"]["perDxGateway"]) == 8
