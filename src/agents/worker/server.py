"""Generic worker agent — serves any agent with type 'worker' via HTTP.

Reads ``AGENT_NAME`` from the environment, loads its configuration from
``hierarchy.json``, and calls ``create_leaf_agent()`` to wire up gateway
MCP tools and HTTP entrypoint.

Usage (Docker CMD)::

    CMD ["opentelemetry-instrument", "python", "-m", "agents.worker.server"]
"""

from __future__ import annotations

import json
import logging
import os

from agents.shared.agent_base import create_leaf_agent

logger = logging.getLogger(__name__)


def _load_agent_config(agent_name: str) -> dict:
    """Load agent configuration from hierarchy.json."""
    hierarchy_path = os.path.join(os.path.dirname(__file__), "..", "hierarchy.json")
    with open(hierarchy_path, encoding="utf-8") as f:
        hierarchy = json.load(f)
    if agent_name not in hierarchy:
        raise ValueError(f"Agent '{agent_name}' not found in hierarchy.json")
    return hierarchy[agent_name]


# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
AGENT_NAME = os.environ.get("AGENT_NAME")
if not AGENT_NAME:
    raise RuntimeError(
        "AGENT_NAME environment variable is required. "
        "Set it to the agent key in hierarchy.json (e.g., 'cost-operations-agent')."
    )

config = _load_agent_config(AGENT_NAME)

# ---------------------------------------------------------------------------
# BedrockAgentCoreApp (via shared leaf agent factory)
# ---------------------------------------------------------------------------
app, entrypoint = create_leaf_agent(
    agent_name=AGENT_NAME,
    system_prompt=config["prompt"],
    model_id=config.get("model", ""),
    allowed_tools=config.get("tools"),
)

if __name__ == "__main__":
    app.run()
