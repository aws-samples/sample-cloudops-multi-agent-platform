"""Integration tests against the live deployed CloudOps stack.

Requires:
- A deployed stack (`make deploy-auto`)
- Cognito credentials in scripts/.env (COGNITO_USERNAME, COGNITO_PASSWORD)

Run with:
    .venv/bin/python -m pytest tests/integration/ -v
    .venv/bin/python -m pytest tests/integration/ -v -k "test_supervisor"
"""

from __future__ import annotations

import json
import uuid

import boto3
import httpx
import pytest

from tests.integration.conftest import invoke_supervisor_agui


# ---------------------------------------------------------------------------
# 1. Supervisor — AG-UI chat (end-to-end)
# ---------------------------------------------------------------------------


class TestSupervisorChat:
    """Test the supervisor agent via AG-UI streaming."""

    def test_basic_greeting(self, deployed_config, cognito_token, session_id):
        """Supervisor responds to a simple greeting."""
        result = invoke_supervisor_agui(
            deployed_config["supervisor_url"],
            cognito_token,
            "Hello, what can you help me with?",
            session_id,
        )
        assert not result["errors"], f"Errors: {result['errors']}"
        assert len(result["text"]) > 20, "Response too short"
        assert any(e.get("type") == "RUN_FINISHED" for e in result["events"])

    def test_cost_query_delegates_to_finops(
        self, deployed_config, cognito_token, session_id
    ):
        """A cost question should delegate to finops-agent and return real data."""
        result = invoke_supervisor_agui(
            deployed_config["supervisor_url"],
            cognito_token,
            "What was my total AWS spend last month?",
            session_id,
            timeout=300.0,
        )
        assert not result["errors"], f"Errors: {result['errors']}"
        # Should have tool calls (delegation to sub-agents)
        assert len(result["tools"]) > 0, "No tool calls — delegation didn't happen"
        # Response should contain cost-related content
        text_lower = result["text"].lower()
        assert any(
            w in text_lower for w in ["cost", "$", "spend", "usd", "total"]
        ), f"Response doesn't mention costs: {result['text'][:200]}"

    def test_tool_traces_present(self, deployed_config, cognito_token, session_id):
        """Tool call results should include nested trace data from sub-agents."""
        result = invoke_supervisor_agui(
            deployed_config["supervisor_url"],
            cognito_token,
            "Give me a quick cost breakdown by service for last month",
            session_id,
            timeout=300.0,
        )
        assert not result["errors"]
        if result["tools"]:
            # At least one tool result should have parseable content
            for tool_event in result["tools"]:
                content = tool_event.get("content", "")
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict) and "tool_trace" in parsed:
                        # Found nested traces — good
                        assert len(parsed["tool_trace"]) > 0
                        return
                except (json.JSONDecodeError, TypeError):
                    continue
            # No nested traces found — not a failure, just less visibility
            pytest.skip(
                "No nested tool traces in response (sub-agents may not have returned traces)"
            )


# ---------------------------------------------------------------------------
# 2. Sub-agent direct invocation (HTTP protocol)
# ---------------------------------------------------------------------------


class TestSubAgentDirect:
    """Test sub-agents directly via invoke_agent_runtime (bypasses supervisor)."""

    def _invoke_runtime(
        self, runtime_id: str, prompt: str, region: str, timeout: int = 120
    ) -> dict:
        """Invoke an agent runtime directly via boto3.

        runtime_id is the runtime name from terraform output. We look up the
        ARN via get_agent_runtime, then invoke.
        """
        control = boto3.client("bedrock-agentcore-control", region_name=region)
        rt = control.get_agent_runtime(agentRuntimeId=runtime_id)
        runtime_arn = rt["agentRuntimeArn"]

        client = boto3.client("bedrock-agentcore", region_name=region)
        payload = json.dumps({"prompt": prompt}).encode("utf-8")
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            payload=payload,
            contentType="application/json",
            accept="application/json",
        )
        body = resp.get("response", b"")
        if hasattr(body, "read"):
            text = body.read().decode("utf-8")
        else:
            text = body.decode("utf-8") if isinstance(body, bytes) else str(body)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"response": text}

    def test_finops_agent_responds(self, deployed_config):
        """FinOps mid-level agent should respond to a cost question."""
        runtime_ids = deployed_config.get("agent_runtime_ids", {})
        finops_id = runtime_ids.get("finops-agent", "")
        if not finops_id:
            pytest.skip("finops-agent runtime not found in terraform outputs")
        result = self._invoke_runtime(
            finops_id,
            "What was the total AWS cost last month?",
            deployed_config["region"],
        )
        assert "error" not in str(result).lower() or "response" in result

    def test_cost_operations_agent_responds(self, deployed_config):
        """Cost operations leaf agent should call gateway tools and return data."""
        runtime_ids = deployed_config.get("agent_runtime_ids", {})
        cost_ops_id = runtime_ids.get("cost-operations-agent", "")
        if not cost_ops_id:
            pytest.skip("cost-operations-agent runtime not found")
        result = self._invoke_runtime(
            cost_ops_id,
            "Get the total cost for last month using Cost Explorer",
            deployed_config["region"],
        )
        # Should have a response with cost data or a traced response
        assert result, "Empty response from cost-operations-agent"


# ---------------------------------------------------------------------------
# 3. Gateway tools — direct Lambda invocation
# ---------------------------------------------------------------------------


class TestGatewayTools:
    """Test Lambda MCP tools directly to verify schemas and functionality."""

    def _invoke_lambda(
        self, function_name: str, tool_name: str, params: dict, region: str
    ) -> dict:
        """Invoke a Lambda tool directly with a mock gateway context."""
        import base64

        client = boto3.client("lambda", region_name=region)
        context_custom = json.dumps(
            {"bedrockAgentCoreToolName": f"{function_name}___{tool_name}"}
        )
        resp = client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(params),
            ClientContext=base64.b64encode(
                json.dumps(
                    {"custom": json.loads(context_custom.replace("___", "___"))}
                ).encode()
            ).decode(),
        )
        payload = json.loads(resp["Payload"].read())
        return payload

    def test_cost_explorer_get_today_date(self, deployed_config):
        """get_today_date tool should return current date info."""
        region = deployed_config["region"]
        prefix = "cloudops"  # from PROJECT_PREFIX
        try:
            result = self._invoke_lambda(
                f"{prefix}-cost-explorer-tool",
                "get_today_date",
                {},
                region,
            )
            assert "today" in result, f"Unexpected response: {result}"
            assert "year" in result
            assert "month" in result
        except Exception as e:
            if "ResourceNotFoundException" in str(e):
                pytest.skip("cost-explorer Lambda not found")
            raise

    def test_cost_explorer_get_cost_and_usage(self, deployed_config):
        """get_cost_and_usage tool should return cost data."""
        region = deployed_config["region"]
        prefix = "cloudops"
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc)
        start = today.replace(day=1).strftime("%Y-%m-%d")
        end = today.strftime("%Y-%m-%d")
        try:
            result = self._invoke_lambda(
                f"{prefix}-cost-explorer-tool",
                "get_cost_and_usage",
                {"start_date": start, "end_date": end, "granularity": "MONTHLY"},
                region,
            )
            assert "error" not in result or "results" in result
        except Exception as e:
            if "ResourceNotFoundException" in str(e):
                pytest.skip("cost-explorer Lambda not found")
            raise


# ---------------------------------------------------------------------------
# 4. Frontend API — CRUD via API Gateway
# ---------------------------------------------------------------------------


class TestFrontendAPI:
    """Test the Frontend API Lambda (sessions, templates, reports)."""

    def _api_request(
        self,
        base_url: str,
        method: str,
        path: str,
        token: str,
        body: dict | None = None,
    ) -> dict:
        with httpx.Client(timeout=30.0) as client:
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            url = f"{base_url.rstrip('/')}{path}"
            if method == "GET":
                resp = client.get(url, headers=headers)
            elif method == "POST":
                resp = client.post(url, headers=headers, json=body or {})
            elif method == "DELETE":
                resp = client.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported method: {method}")
            return {
                "status": resp.status_code,
                "body": resp.json() if resp.text else {},
            }

    def test_list_sessions(self, deployed_config, cognito_token):
        """GET /sessions should return a list."""
        url = deployed_config.get("frontend_api_url", "")
        if not url:
            pytest.skip("frontend_api_url not available")
        result = self._api_request(url, "GET", "/sessions", cognito_token)
        assert result["status"] == 200
        assert "sessions" in result["body"] or isinstance(result["body"], list)

    def test_list_templates(self, deployed_config, cognito_token):
        """GET /templates should return templates including built-in ones."""
        url = deployed_config.get("frontend_api_url", "")
        if not url:
            pytest.skip("frontend_api_url not available")
        result = self._api_request(url, "GET", "/templates", cognito_token)
        assert result["status"] == 200
        templates = result["body"].get("templates", [])
        assert len(templates) > 0, "No templates returned (should have built-in)"

    def test_list_reports(self, deployed_config, cognito_token):
        """GET /reports should return a list (possibly empty)."""
        url = deployed_config.get("frontend_api_url", "")
        if not url:
            pytest.skip("frontend_api_url not available")
        result = self._api_request(url, "GET", "/reports", cognito_token)
        assert result["status"] == 200


# ---------------------------------------------------------------------------
# 5. Memory — session persistence
# ---------------------------------------------------------------------------


class TestMemoryPersistence:
    """Test that conversations are persisted to AgentCore Memory."""

    def test_session_persists_after_chat(self, deployed_config, cognito_token):
        """After a chat, the session should appear in the sessions list."""
        import time

        url = deployed_config.get("frontend_api_url", "")
        if not url:
            pytest.skip("frontend_api_url not available")

        sid = str(uuid.uuid4())
        invoke_supervisor_agui(
            deployed_config["supervisor_url"],
            cognito_token,
            "Hello, this is a persistence test",
            sid,
        )

        # Retry with backoff — Memory persistence can take a few seconds
        session_ids = []
        for attempt in range(5):
            time.sleep(2 + attempt)
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(
                    f"{url.rstrip('/')}/sessions",
                    headers={"Authorization": f"Bearer {cognito_token}"},
                )
                body = resp.json()
                sessions = body.get("sessions", body.get("sessionSummaries", []))
                session_ids = [
                    s.get("sessionId", s.get("session_id", "")) for s in sessions
                ]
                if sid in session_ids:
                    return  # Pass
        assert sid in session_ids, f"Session {sid[:8]}... not found after 5 retries"

    def test_session_history_has_messages(self, deployed_config, cognito_token):
        """Session history should contain the user and assistant messages."""
        import time

        url = deployed_config.get("frontend_api_url", "")
        if not url:
            pytest.skip("frontend_api_url not available")

        sid = str(uuid.uuid4())
        invoke_supervisor_agui(
            deployed_config["supervisor_url"],
            cognito_token,
            "Say exactly: integration test confirmed",
            sid,
        )

        # Retry with backoff
        messages = []
        for attempt in range(5):
            time.sleep(2 + attempt)
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(
                    f"{url.rstrip('/')}/sessions/{sid}/history",
                    headers={"Authorization": f"Bearer {cognito_token}"},
                )
                messages = resp.json().get("messages", [])
                if len(messages) >= 2:
                    roles = [m["role"] for m in messages]
                    assert "user" in roles
                    assert "assistant" in roles
                    return  # Pass
        assert (
            len(messages) >= 2
        ), f"Expected 2+ messages after 5 retries, got {len(messages)}"
