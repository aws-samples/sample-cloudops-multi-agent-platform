"""Dynamic system prompt generation from the DynamoDB agent registry.

Builds system prompts by replacing an ``{agent_listing}`` placeholder in a
static template with a dynamically generated list of child agents read from
the registry at startup. Automatically injects the current date into all prompts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agents.shared.registry import load_agent_registry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _inject_date(prompt: str) -> str:
    """Inject today's date into a prompt. Replaces {today_date} if present,
    otherwise prepends a date line."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if "{today_date}" in prompt:
        return prompt.replace("{today_date}", today)
    return f"Today's date is {today}.\n\n{prompt}"


def build_dynamic_prompt(
    static_template: str,
    agent_name: str,
    table_name: str,
) -> str:
    """Build a system prompt with a dynamic agent listing.

    Queries the DynamoDB agent registry for child agents and injects the
    listing into *static_template* by replacing the ``{agent_listing}``
    placeholder.

    Args:
        static_template: Prompt template containing an ``{agent_listing}``
            placeholder.
        agent_name: The current agent's name.  For the Supervisor use
            ``"supervisor"`` — this queries top-level agents
            (``parent_filter=None``).  For mid-level agents the value is
            used directly as ``parent_filter``.
        table_name: DynamoDB registry table name.

    Returns:
        The complete prompt string with the agent listing filled in.
    """
    # Supervisor sees agents whose parent_agent is "supervisor";
    # mid-level agents see their own children.
    parent_filter = agent_name

    registry = load_agent_registry(table_name=table_name, parent_filter=parent_filter)

    # Keep only enabled entries
    enabled = [entry for entry in registry if entry.get("enabled", False)]

    listing = _build_agent_listing(enabled)
    prompt = static_template.replace("{agent_listing}", listing)
    return _inject_date(prompt)


def _build_agent_listing(registry: list[dict]) -> str:
    """Format a list of registry entries into a prompt-friendly agent listing.

    Args:
        registry: Filtered (enabled-only) registry entries.

    Returns:
        A formatted string suitable for embedding in a system prompt.
    """
    if not registry:
        return (
            "No child agents are currently deployed. "
            "Inform the user that no agents are available and suggest "
            "checking the deployment status."
        )

    lines = ["Available agents:"]
    for entry in registry:
        name = entry.get("agent_name", "")
        desc = entry.get("description", "")
        lines.append(f"- **{name}**: {desc}")
    return "\n".join(lines)
