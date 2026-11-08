"""G-06: ordered hard policies and the unified tool action mapping."""

from __future__ import annotations

import pytest

from config import settings
from execution.actions import action_for_tool, policy_actions_for_tool
from execution.access import AccessController, policy_block
from policy.engine import PolicyDenied, PolicyEngine, PolicyStatement
def test_no_match_allows():
    engine = PolicyEngine([PolicyStatement("tool.use", "shell_exec", "deny")])
    assert engine.evaluate("tool.use", "read_file").allowed is True


def test_last_match_wins_within_source():
    engine = PolicyEngine(
        [
            PolicyStatement("tool.use", "shell_exec", "deny"),
            PolicyStatement("tool.use", "shell_exec", "allow"),
        ]
    )
    assert engine.evaluate("tool.use", "shell_exec").allowed is True


def test_source_priority_overrides_global():
    engine = PolicyEngine([PolicyStatement("websearch.use", "*", "deny", "global")])
    engine = engine.with_statements(
        [{"action": "websearch.use", "resource": "*", "effect": "allow", "source": "agent"}],
        "agent",
    )
    assert engine.evaluate("websearch.use", "web_search").allowed is True

    engine = engine.with_statements(
        [{"action": "websearch.use", "resource": "*", "effect": "deny"}], "managed"
    )
    assert engine.evaluate("websearch.use", "web_search").allowed is False


def test_wildcards_are_case_insensitive():
    engine = PolicyEngine([PolicyStatement("tool.use", "MCP_*", "deny")])
    assert engine.evaluate("tool.use", "mcp_github_search").allowed is False
    assert engine.evaluate("tool.use", "read_file").allowed is True


def test_unknown_action_is_ignored():
    engine = PolicyEngine([PolicyStatement("not.an.action", "*", "deny")])
    assert engine.evaluate("not.an.action", "anything").allowed is True


def test_check_raises_with_contract_message():
    engine = PolicyEngine([PolicyStatement("tool.use", "shell_exec", "deny")])
    with pytest.raises(PolicyDenied) as exc:
        engine.check("tool.use", "shell_exec")
    assert "Blocked by configuration policy" in str(exc.value)


def test_settings_policies_are_loaded(monkeypatch):
    monkeypatch.setattr(
        settings,
        "policies",
        [{"action": "tool.use", "resource": "shell_exec", "effect": "deny"}],
    )
    assert policy_block("shell_exec") == "Blocked by configuration policy"
    assert policy_block("read_file") is None


def test_controller_maps_websearch_and_mcp():
    engine = PolicyEngine([PolicyStatement("websearch.use", "*", "deny")])
    controller = AccessController(policy=engine)
    assert controller.block_reason("web_search") == "Blocked by configuration policy"

    engine = PolicyEngine([PolicyStatement("mcp.use", "*", "deny")])
    assert AccessController(policy=engine).block_reason("mcp_github_search") == (
        "Blocked by configuration policy"
    )


def test_action_mapping():
    assert action_for_tool("read_file") == "read"
    assert action_for_tool("write_file") == "edit"
    assert action_for_tool("task") == "subagent"
    assert action_for_tool("mcp_x") == "mcp_tool"
    assert action_for_tool("plugin_foo") == "tool.use"
    assert ("websearch.use", "web_search") in policy_actions_for_tool("web_search")


def test_agent_permission_denies_only_in_scope(monkeypatch):
    import execution.access as access

    monkeypatch.setattr(access, 'get_policy_engine', lambda: PolicyEngine([]))
    token = access.set_agent_policy(access.statements_from_permission({'edit': 'deny'}))
    try:
        assert access.policy_block('write_file')
        assert access.policy_block('read_file') is None
    finally:
        access.reset_agent_policy(token)
    assert access.policy_block('write_file') is None
