# Backward-compat re-exports. All shared code now lives in agents.shared.
# This module can be removed once no external code imports from shared.*.

from agents.shared.aggregation import aggregate_results
from agents.shared.aws_utils import (
    CrossAccountAccessError,
    extract_account_from_arn,
    get_aws_client,
)
from agents.shared.frontier import invoke_devops_agent, invoke_security_agent
from agents.shared.registry import build_agent_tools, load_agent_registry

__all__ = [
    "CrossAccountAccessError",
    "aggregate_results",
    "build_agent_tools",
    "extract_account_from_arn",
    "get_aws_client",
    "invoke_devops_agent",
    "invoke_security_agent",
    "load_agent_registry",
]
