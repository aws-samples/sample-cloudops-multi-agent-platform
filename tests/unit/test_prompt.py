"""Unit tests for agents.shared.prompt — dynamic system prompt generation."""

from unittest.mock import patch

from agents.shared.prompt import _build_agent_listing, build_dynamic_prompt


class TestBuildAgentListing:
    """Tests for the internal _build_agent_listing helper."""

    def test_empty_registry_returns_no_agents_message(self):
        result = _build_agent_listing([])
        assert "No child agents are currently deployed" in result
        assert "checking the deployment status" in result

    def test_single_agent(self):
        registry = [
            {"agent_name": "finops-agent", "description": "Financial operations"}
        ]
        result = _build_agent_listing(registry)
        assert result.startswith("Available agents:")
        assert "- **finops-agent**: Financial operations" in result

    def test_multiple_agents(self):
        registry = [
            {"agent_name": "finops-agent", "description": "Financial ops"},
            {"agent_name": "governance-agent", "description": "Governance ops"},
        ]
        result = _build_agent_listing(registry)
        assert "- **finops-agent**: Financial ops" in result
        assert "- **governance-agent**: Governance ops" in result

    def test_missing_description_defaults_to_empty(self):
        registry = [{"agent_name": "test-agent"}]
        result = _build_agent_listing(registry)
        assert "- **test-agent**: " in result


class TestBuildDynamicPrompt:
    """Tests for the public build_dynamic_prompt function."""

    TEMPLATE = "You are an agent.\n\n{agent_listing}\n\nDelegation rules apply."

    @patch("agents.shared.prompt.load_agent_registry")
    def test_supervisor_uses_own_name_as_parent_filter(self, mock_load):
        mock_load.return_value = [
            {"agent_name": "finops-agent", "description": "FinOps", "enabled": True}
        ]
        build_dynamic_prompt(self.TEMPLATE, "supervisor", "my-table")
        mock_load.assert_called_once_with(
            table_name="my-table", parent_filter="supervisor"
        )

    @patch("agents.shared.prompt.load_agent_registry")
    def test_midlevel_uses_own_name_as_parent_filter(self, mock_load):
        mock_load.return_value = []
        build_dynamic_prompt(self.TEMPLATE, "finops-agent", "my-table")
        mock_load.assert_called_once_with(
            table_name="my-table", parent_filter="finops-agent"
        )

    @patch("agents.shared.prompt.load_agent_registry")
    def test_replaces_placeholder_with_listing(self, mock_load):
        mock_load.return_value = [
            {"agent_name": "sec-agent", "description": "Security", "enabled": True}
        ]
        result = build_dynamic_prompt(self.TEMPLATE, "supervisor", "t")
        assert "{agent_listing}" not in result
        assert "- **sec-agent**: Security" in result
        assert "You are an agent." in result
        assert "Delegation rules apply." in result

    @patch("agents.shared.prompt.load_agent_registry")
    def test_filters_disabled_agents(self, mock_load):
        mock_load.return_value = [
            {"agent_name": "enabled-agent", "description": "On", "enabled": True},
            {"agent_name": "disabled-agent", "description": "Off", "enabled": False},
        ]
        result = build_dynamic_prompt(self.TEMPLATE, "supervisor", "t")
        assert "enabled-agent" in result
        assert "disabled-agent" not in result

    @patch("agents.shared.prompt.load_agent_registry")
    def test_no_agents_produces_fallback_message(self, mock_load):
        mock_load.return_value = []
        result = build_dynamic_prompt(self.TEMPLATE, "supervisor", "t")
        assert "No child agents are currently deployed" in result
        assert "Delegation rules apply." in result

    @patch("agents.shared.prompt.load_agent_registry")
    def test_all_disabled_produces_fallback_message(self, mock_load):
        mock_load.return_value = [
            {"agent_name": "a", "description": "x", "enabled": False},
        ]
        result = build_dynamic_prompt(self.TEMPLATE, "supervisor", "t")
        assert "No child agents are currently deployed" in result
