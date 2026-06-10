"""Follow-up suggestion generation for frontend-facing agents.

Makes a lightweight LLM call to generate 2-4 contextual follow-up questions
based on the conversation turn.
"""

from __future__ import annotations

import json
import logging
import re

from strands import Agent
from strands.models import BedrockModel

logger = logging.getLogger(__name__)


def generate_suggestions(model_id: str, prompt: str, response_text: str) -> list[str]:
    """Generate 2-4 contextual follow-up questions."""
    if not response_text.strip():
        return []
    try:
        model = BedrockModel(model_id=model_id)
        agent = Agent(
            model=model,
            system_prompt="Return only a JSON array of strings. No other text.",
        )
        suggestion_prompt = (
            f"Based on this conversation:\n"
            f"User: {prompt[:500]}\n"
            f"Assistant: {response_text[:1500]}\n\n"
            f"Generate 2-4 brief follow-up questions the user might ask next "
            f"about AWS cloud operations. Return ONLY a JSON array of strings."
        )
        result = agent(suggestion_prompt)
        text = result.message["content"][0]["text"]
        json_match = re.search(r"\[[\s\S]*?\]", text)
        if json_match:
            return json.loads(json_match.group())
        return json.loads(text)
    except Exception as exc:
        logger.warning("Suggestion generation failed: %s", exc)
        return []
