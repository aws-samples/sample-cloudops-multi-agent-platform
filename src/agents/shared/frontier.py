"""Frontier agent tool wrappers for invoking managed AWS agents.

Provides ``@tool``-decorated functions that wrap the boto3
``InvokeAgentRuntime`` API for the AWS DevOps and Security frontier
agents. These tools can be used by any leaf agent that needs to
delegate to a managed AWS agent.
"""

from __future__ import annotations

import json
import logging

from strands import tool

from agents.shared.aws_utils import get_aws_client

logger = logging.getLogger(__name__)


def _invoke_frontier_agent(
    prompt: str,
    agent_arn: str,
    agent_label: str,
) -> dict:
    """Invoke a frontier agent via InvokeAgentRuntime and return a result dict.

    Args:
        prompt: The task description or question to send.
        agent_arn: The AgentCore Runtime ARN of the frontier agent.
        agent_label: Human-readable label for error messages.

    Returns:
        A dict with ``agent_name``, ``status``, and ``data`` or ``error_message``.
    """
    try:
        client = get_aws_client("bedrock-agentcore")
        payload = json.dumps({"prompt": prompt}).encode()

        response = client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            runtimeSessionId=f"frontier-{agent_label}",
            payload=payload,
        )

        # Process streaming response
        content_type = response.get("contentType", "")
        chunks: list[str] = []

        if "text/event-stream" in content_type:
            for line in response["response"].iter_lines(chunk_size=10):
                if line:
                    decoded = line.decode("utf-8")
                    if decoded.startswith("data: "):
                        chunks.append(decoded[6:])
        elif response.get("contentType") == "application/json":
            for chunk in response.get("response", []):
                chunks.append(chunk.decode("utf-8"))

        result_text = "\n".join(chunks) if chunks else "No response from frontier agent"

        return {
            "agent_name": agent_label,
            "status": "success",
            "data": result_text,
        }

    except Exception as exc:
        logger.error(
            "Error invoking frontier agent '%s': %s", agent_label, exc, exc_info=True
        )
        return {
            "agent_name": agent_label,
            "status": "error",
            "error_message": f"Frontier agent '{agent_label}' failed: {exc}",
        }


@tool(
    name="invoke_devops_agent",
    description=(
        "Invoke the AWS DevOps frontier agent for incident triaging. "
        "Sends a prompt describing the incident and returns the agent's "
        "analysis and recommended resolution steps."
    ),
)
def invoke_devops_agent(prompt: str, agent_id: str) -> dict:
    """Invoke the AWS DevOps frontier agent via InvokeAgentRuntime.

    Args:
        prompt: The incident description or operational question.
        agent_id: The AgentCore Runtime ARN of the DevOps frontier agent.

    Returns:
        A dict with ``agent_name``, ``status``, and ``data`` or ``error_message``.
    """
    return _invoke_frontier_agent(prompt, agent_id, "devops-frontier")


@tool(
    name="invoke_security_agent",
    description=(
        "Invoke the AWS Security frontier agent for security assessments. "
        "Sends a prompt describing the security concern and returns the "
        "agent's findings and recommendations."
    ),
)
def invoke_security_agent(prompt: str, agent_id: str) -> dict:
    """Invoke the AWS Security frontier agent via InvokeAgentRuntime.

    Args:
        prompt: The security assessment request or concern.
        agent_id: The AgentCore Runtime ARN of the Security frontier agent.

    Returns:
        A dict with ``agent_name``, ``status``, and ``data`` or ``error_message``.
    """
    return _invoke_frontier_agent(prompt, agent_id, "security-frontier")
