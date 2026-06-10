"""Shared result aggregation for orchestrating agents.

Collects Sub-Agent results (successes, errors, timeouts) into a unified
response structure. Used by both the Supervisor and mid-level agents.
No sub-task result is lost.
"""

from __future__ import annotations


def aggregate_results(results: list[dict]) -> dict:
    """Aggregate Sub-Agent results into a unified response.

    Each *result* dict is expected to have:
        - ``agent_name`` (str): Which Sub-Agent produced this result.
        - ``status`` (str): One of ``"success"``, ``"error"``, or ``"timeout"``.
        - ``data`` (str, optional): Present when ``status == "success"``.
        - ``error_message`` (str, optional): Present on error/timeout.

    Returns:
        A dict with the following keys:

        - ``successes`` — list of ``{"agent_name": ..., "data": ...}`` dicts.
        - ``errors`` — list of ``{"agent_name": ..., "error_message": ...}`` dicts.
        - ``timeouts`` — list of ``{"agent_name": ..., "error_message": ...}`` dicts.
        - ``summary`` — human-readable one-line summary string.
    """
    successes: list[dict] = []
    errors: list[dict] = []
    timeouts: list[dict] = []

    for result in results:
        status = result.get("status", "error")
        agent_name = result.get("agent_name", "unknown")

        if status == "success":
            successes.append(
                {
                    "agent_name": agent_name,
                    "data": result.get("data", ""),
                }
            )
        elif status == "timeout":
            timeouts.append(
                {
                    "agent_name": agent_name,
                    "error_message": result.get("error_message", "Timed out"),
                }
            )
        else:
            errors.append(
                {
                    "agent_name": agent_name,
                    "error_message": result.get("error_message", "Unknown error"),
                }
            )

    summary = (
        f"{len(successes)} succeeded, {len(errors)} failed, {len(timeouts)} timed out"
    )

    return {
        "successes": successes,
        "errors": errors,
        "timeouts": timeouts,
        "summary": summary,
    }
