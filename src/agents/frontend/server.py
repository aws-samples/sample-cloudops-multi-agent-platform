"""Generic frontend agent — serves any agent with type 'frontend' via AG-UI.

Reads ``AGENT_NAME`` from the environment, loads its configuration from
``hierarchy.json``, and calls ``create_frontend_agent()`` to wire up AG-UI
streaming, memory, suggestions, and report generation automatically.

Usage (Docker CMD)::

    CMD ["opentelemetry-instrument", "uvicorn", "agents.frontend.server:app",
         "--host", "0.0.0.0", "--port", "8080"]
"""

from __future__ import annotations

import json
import logging
import os

from agents.shared.agent_base import create_frontend_agent

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
        "Set it to the agent key in hierarchy.json (e.g., 'supervisor')."
    )

config = _load_agent_config(AGENT_NAME)

# Pick the frontend's execution mode from the promoted agent's hierarchy
# entry. Previously this was hardcoded to "mid_level", which broke
# solo-leaf-as-frontend: a worker promoted to frontend got routed to the
# mid-level branch that looks up children in the DynamoDB registry,
# found none, and ran with zero tools — the hallucination guardrail
# then correctly refused to answer every prompt.
#
# Rule: a non-empty `children` array means this agent routes via
# child-agent delegation (mid_level). Otherwise it's a leaf that
# loads gateway MCP tools directly.
_children = config.get("children") or []
_resolved_agent_type = "mid_level" if _children else "leaf"
logger.info(
    "Resolved agent_type for frontend %s: %s (children=%d, tools=%s)",
    AGENT_NAME,
    _resolved_agent_type,
    len(_children),
    config.get("tools"),
)

# ---------------------------------------------------------------------------
# FastAPI Application (via shared frontend agent factory)
# ---------------------------------------------------------------------------
app = create_frontend_agent(
    agent_name=AGENT_NAME,
    agent_description=config.get("description", AGENT_NAME),
    prompt_template=config["prompt"],
    agent_type=_resolved_agent_type,
    model_id=config.get("model", ""),
    memory_enabled=config.get("memory", True),
    suggestions_enabled=config.get("suggestions", True),
    parent_filter=AGENT_NAME,
    allowed_tools=config.get("tools"),
)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)  # nosec B104 — container must bind all interfaces


if __name__ == "__main__":
    main()
