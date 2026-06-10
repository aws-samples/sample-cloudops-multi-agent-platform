"""AgentCore Gateway MCP client with SigV4 authentication.

Provides a reusable helper for leaf agents to connect to the AgentCore
Gateway using IAM credentials (SigV4-signed requests).
"""

from __future__ import annotations

import logging
import os

import botocore.session
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

logger = logging.getLogger(__name__)


class _SigV4Auth(httpx.Auth):
    """httpx auth handler that signs requests with AWS SigV4."""

    def __init__(self, service: str = "bedrock-agentcore", region: str | None = None):
        session = botocore.session.get_session()
        credentials = session.get_credentials()
        if credentials is None:
            raise RuntimeError(
                "No AWS credentials found via botocore session. "
                "Ensure the runtime execution role is configured or "
                "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY are set."
            )
        self._credentials = credentials.get_frozen_credentials()
        self._service = service
        self._region = region or os.environ.get("AWS_REGION", "us-east-1")

    def auth_flow(self, request):
        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            headers=dict(request.headers),
            data=request.content,
        )
        SigV4Auth(self._credentials, self._service, self._region).add_auth(aws_request)
        for key, value in aws_request.headers.items():
            request.headers[key] = value
        yield request


def get_gateway_mcp_client():
    """Return an MCPClient connected to the AgentCore Gateway with SigV4 auth.

    Returns None if AGENTCORE_GATEWAY_ENDPOINT is not set.
    """
    gateway_url = os.environ.get("AGENTCORE_GATEWAY_ENDPOINT", "")
    if not gateway_url:
        logger.warning("AGENTCORE_GATEWAY_ENDPOINT not set — no gateway tools")
        return None

    try:
        from mcp.client.streamable_http import streamablehttp_client
        from strands.tools.mcp.mcp_client import MCPClient

        auth = _SigV4Auth()
        logger.info("Connecting to AgentCore Gateway at %s (SigV4)", gateway_url)
        return MCPClient(lambda: streamablehttp_client(gateway_url, auth=auth))
    except Exception as exc:
        logger.error("Gateway connection failed: %s", exc, exc_info=True)
        return None
