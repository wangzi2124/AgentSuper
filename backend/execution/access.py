"""Access control facade: permissions first, policies last (gap G-06).

``PermissionManager`` answers "may this path/command be touched?" and can be
relaxed by a turn-scoped approval. ``PolicyEngine`` is a hard second gate that
approval can never widen. Tool execution asks this module so the order is
identical everywhere.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

from execution.actions import action_for_tool, policy_actions_for_tool
from execution.permission import PermissionManager
from policy.engine import PolicyDecision, PolicyEngine, PolicyStatement, get_policy_engine


@dataclass
class AccessController:
    permission: PermissionManager | None = None
    policy: PolicyEngine | None = None

    def _policy(self) -> PolicyEngine:
        return self.policy if self.policy is not None else get_policy_engine()

    def permission_effect(self, path: str, operation: str) -> str | None:
        if self.permission is None:
            return None
        return self.permission.check(path, operation)

    def evaluate_tool(self, tool_name: str) -> PolicyDecision:
        engine = self._policy()
        decision: PolicyDecision | None = None
        for action, resource in policy_actions_for_tool(tool_name):
            current = engine.evaluate(action, resource)
            if not current.allowed:
                return current
            decision = current
        return decision or PolicyDecision(True, "tool.use", str(tool_name))

    def block_reason(self, tool_name: str) -> str | None:
        decision = self.evaluate_tool(tool_name)
        return None if decision.allowed else decision.reason


_CONTROLLER = AccessController()
_AGENT_POLICY: contextvars.ContextVar[tuple[PolicyStatement, ...]] = contextvars.ContextVar(
    "agent_policy", default=()
)


def statements_from_permission(permission: dict[str, Any] | None) -> tuple[PolicyStatement, ...]:
    """Convert an agent's ``permissions`` map into agent-scoped statements."""
    statements: list[PolicyStatement] = []
    for action, effect in (permission or {}).items():
        act = str(action or "").strip().lower()
        if not act:
            continue
        if isinstance(effect, dict):
            for resource, value in effect.items():
                value_text = str(value).strip().lower()
                if value_text in ("allow", "deny"):
                    statements.append(
                        PolicyStatement(act, str(resource), value_text, "agent")  # type: ignore[arg-type]
                    )
        else:
            value_text = str(effect).strip().lower()
            if value_text in ("allow", "deny"):
                statements.append(
                    PolicyStatement(act, "*", value_text, "agent")  # type: ignore[arg-type]
                )
    return tuple(statements)


def set_agent_policy(statements: tuple[PolicyStatement, ...]):
    return _AGENT_POLICY.set(statements)


def reset_agent_policy(token) -> None:
    _AGENT_POLICY.reset(token)


def get_access_controller() -> AccessController:
    return _CONTROLLER


def policy_block_for(action: str, resource: str) -> str | None:
    """Evaluate one policy action, including the current agent's statements."""
    engine = get_policy_engine()
    extra = _AGENT_POLICY.get()
    if extra:
        engine = engine.with_statements(list(extra), "agent")
    decision = engine.evaluate(action, resource)
    return None if decision.allowed else decision.reason


def policy_block(tool_name: str) -> str | None:
    """Return a human-readable hard-deny reason, or ``None`` when allowed."""
    for action, resource in policy_actions_for_tool(tool_name):
        blocked = policy_block_for(action, resource)
        if blocked:
            return blocked
    return None


def tool_action(tool_name: str) -> str:
    return action_for_tool(tool_name)


__all__ = [
    "AccessController",
    "get_access_controller",
    "policy_block",
    "policy_block_for",
    "reset_agent_policy",
    "set_agent_policy",
    "statements_from_permission",
    "tool_action",
]
