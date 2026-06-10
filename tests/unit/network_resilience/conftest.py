"""Pytest configuration for network-resiliency tests.

The MCP Lambda lives at ``src/lambda/mcp/network-resilience/`` and its package
is ``network_resilience``. It's not on Python's default path (that's resolved
at Lambda cold start from the zip root), so these tests add it explicitly.
Tests also need ``handler`` (the top-level module in the Lambda dir).
"""

from __future__ import annotations

import sys
from pathlib import Path

_LAMBDA_DIR = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "lambda"
    / "mcp"
    / "network-resilience"
)

if str(_LAMBDA_DIR) not in sys.path:
    sys.path.insert(0, str(_LAMBDA_DIR))
