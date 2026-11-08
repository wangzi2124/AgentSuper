"""Unified action model for permissions and policies (opencode gap G-10).

Tools, skills, subagents and network calls all funnel through a small, stable
set of action names. Call sites must not invent their own strings.
"""

from __future__ import annotations

from typing import Final

READ: Final = "read"
EDIT: Final = "edit"
GLOB: Final = "glob"
GREP: Final = "grep"
SHELL: Final = "shell"
SUBAGENT: Final = "subagent"
SKILL: Final = "skill"
QUESTION: Final = "question"
WEBFETCH: Final = "webfetch"
WEBSEARCH: Final = "websearch"
EXTERNAL_DIRECTORY: Final = "external_directory"
MCP_TOOL: Final = "mcp_tool"
PROVIDER_USE: Final = "provider.use"
TOOL_USE: Final = "tool.use"
MCP_USE: Final = "mcp.use"
SKILL_USE: Final = "skill.use"
PLUGIN_USE: Final = "plugin.use"
WEBSEARCH_USE: Final = "websearch.use"

_TOOL_ACTIONS: dict[str, str] = {
    "read_file": READ,
    "write_file": EDIT,
    "edit_file": EDIT,
    "append_file": EDIT,
    "delete_file": EDIT,
    "create_directory": EDIT,
    "apply_patch": EDIT,
    "ls": GLOB,
    "glob": GLOB,
    "grep": GREP,
    "shell_exec": SHELL,
    "task": SUBAGENT,
    "web_search": WEBSEARCH,
    "web_fetch": WEBFETCH,
    "http_request": WEBFETCH,
    "get_weather": WEBFETCH,
    "ask_question": QUESTION,
    "question": QUESTION,
}


def action_for_tool(tool_name: str) -> str:
    """Map a tool name to its permission action (unknown tools → tool.use)."""
    name = str(tool_name or "").strip()
    if name in _TOOL_ACTIONS:
        return _TOOL_ACTIONS[name]
    if name.startswith("load_skill"):
        return SKILL
    if name.startswith("mcp_"):
        return MCP_TOOL
    return TOOL_USE


def policy_actions_for_tool(tool_name: str) -> list[tuple[str, str]]:
    """Policy (action, resource) pairs to evaluate for a tool invocation."""
    name = str(tool_name or "").strip()
    action = action_for_tool(name)
    targets: list[tuple[str, str]] = [(TOOL_USE, name)]
    if action != TOOL_USE:
        targets.append((action, name))
    if action == WEBSEARCH:
        targets.append((WEBSEARCH_USE, name))
    elif action == SKILL:
        targets.append((SKILL_USE, name))
    elif action == MCP_TOOL:
        targets.append((MCP_USE, name))
    return targets


__all__ = [
    "EDIT",
    "EXTERNAL_DIRECTORY",
    "GLOB",
    "GREP",
    "MCP_TOOL",
    "MCP_USE",
    "PLUGIN_USE",
    "PROVIDER_USE",
    "QUESTION",
    "READ",
    "SHELL",
    "SKILL",
    "SKILL_USE",
    "SUBAGENT",
    "TOOL_USE",
    "WEBFETCH",
    "WEBSEARCH",
    "WEBSEARCH_USE",
    "action_for_tool",
    "policy_actions_for_tool",
]
