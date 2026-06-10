"""Shared fixtures for integration tests.

Requires a deployed stack and Cognito credentials in scripts/.env.
Run with: .venv/bin/python -m pytest tests/integration/ -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import boto3
import httpx
import pytest


def _load_env(path: str) -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _tf_output(name: str) -> str:
    try:
        raw = subprocess.check_output(
            ["terraform", "-chdir=terraform", "output", "-no-color", "-raw", name],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except subprocess.CalledProcessError:
        return ""
    lines = [
        l
        for l in raw.splitlines()
        if l
        and not l.startswith(
            ("Warning:", "\u2502", "\u2577", "\u2575", "The parameter")
        )
        and "instead." not in l
    ]
    return lines[0] if lines else ""


# ---------------------------------------------------------------------------
# Load env and terraform outputs once at module level
# ---------------------------------------------------------------------------
_load_env(".env")
_load_env("scripts/.env")

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_PROFILE = os.environ.get("AWS_PROFILE", "")
COGNITO_USERNAME = os.environ.get("COGNITO_USERNAME", "")
COGNITO_PASSWORD = os.environ.get("COGNITO_PASSWORD", "")


@pytest.fixture(scope="session")
def deployed_config():
    """Load all Terraform outputs needed for integration tests."""
    config = {
        "region": AWS_REGION,
        "supervisor_url": _tf_output("supervisor_url"),
        "cognito_client_id": _tf_output("cognito_app_client_id"),
        "gateway_id": _tf_output("gateway_id"),
        "gateway_endpoint": _tf_output("gateway_endpoint"),
        "frontend_api_url": _tf_output("frontend_api_url"),
        "memory_id": _tf_output("agentcore_memory_id"),
        "agent_runtime_ids": {},
        "agent_endpoint_names": {},
    }
    # Parse JSON map outputs for sub-agent runtime IDs
    for key in ("agent_runtime_ids", "agent_endpoint_names"):
        try:
            raw = subprocess.check_output(
                ["terraform", "-chdir=terraform", "output", "-no-color", "-json", key],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            import re

            raw = re.sub(r"(?s)╷.*?╵", "", raw).strip()
            raw = "\n".join(
                l
                for l in raw.splitlines()
                if not l.startswith(("Warning:", "│", "The parameter"))
                and "instead." not in l
            )
            config[key] = json.loads(raw)
        except Exception:
            pass

    if not config["supervisor_url"]:
        pytest.skip("No deployed stack found (terraform outputs missing)")
    return config


@pytest.fixture(scope="session")
def cognito_token(deployed_config):
    """Authenticate with Cognito and return an ID token."""
    if not COGNITO_USERNAME or not COGNITO_PASSWORD:
        pytest.skip("COGNITO_USERNAME/COGNITO_PASSWORD not set in scripts/.env")
    client = boto3.client("cognito-idp", region_name=deployed_config["region"])
    resp = client.initiate_auth(
        ClientId=deployed_config["cognito_client_id"],
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": COGNITO_USERNAME, "PASSWORD": COGNITO_PASSWORD},
    )
    return resp["AuthenticationResult"]["IdToken"]


@pytest.fixture
def session_id():
    """Generate a unique session ID for each test."""
    return str(uuid.uuid4())


def invoke_supervisor_agui(
    url: str,
    token: str,
    prompt: str,
    session_id: str,
    template_id: str | None = None,
    timeout: float = 180.0,
) -> dict:
    """Invoke the supervisor via AG-UI and parse the SSE stream.

    Returns a dict with keys: text, reasoning, tools, errors, events.
    """
    # Derive actor_id from Cognito username (same sanitization as frontend/Lambda)
    email = COGNITO_USERNAME
    actor_id = (
        email.replace("@", "_at_").replace(".", "_") if email else "integration-test"
    )

    payload = {
        "threadId": str(uuid.uuid4()),
        "runId": str(uuid.uuid4()),
        "messages": [{"id": str(uuid.uuid4()), "role": "user", "content": prompt}],
        "state": {},
        "tools": [],
        "context": [],
        "forwardedProps": {
            "session_id": session_id,
            "actor_id": actor_id,
            **({"template_id": template_id} if template_id else {}),
        },
    }
    result = {"text": "", "reasoning": "", "tools": [], "errors": [], "events": []}

    with httpx.Client(timeout=timeout) as client:
        with client.stream(
            "POST",
            url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        ) as resp:
            if resp.status_code != 200:
                resp.read()
                result["errors"].append(f"HTTP {resp.status_code}: {resp.text[:500]}")
                return result

            buffer = ""
            for chunk in resp.iter_bytes():
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line.startswith("data: "):
                        continue
                    try:
                        event = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    result["events"].append(event)
                    etype = event.get("type", "")
                    if etype == "TEXT_MESSAGE_CONTENT":
                        result["text"] += event.get("delta", "")
                    elif etype in ("REASONING_MESSAGE_CONTENT", "REASONING_CONTENT"):
                        result["reasoning"] += event.get("delta", "")
                    elif etype == "TOOL_CALL_RESULT":
                        result["tools"].append(event)
                    elif etype == "RUN_ERROR":
                        result["errors"].append(event.get("message", "unknown"))
    return result
