"""Configuration policy layer (opencode gap G-06).

Policies are *hard* rules: they can only tighten behaviour, never grant it.
They are evaluated after permissions/approval and before an action runs. A deny
is final and surfaces as ``Blocked by configuration policy``.

Evaluation is ordered by source priority (global < project < agent < managed)
and, within a source, by declaration order. The **last** matching statement
wins; no match means allow.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Iterable, Literal

from config import get_settings

PolicyEffect = Literal["allow", "deny"]

SOURCE_PRIORITY = {
    "global": 0,
    "project": 1,
    "agent": 2,
    "managed": 3,
}

ACTIONS = frozenset(
    {
        "provider.use",
        "tool.use",
        "integration.connect",
        "integration.use",
        "mcp.use",
        "skill.use",
        "plugin.use",
        "websearch.use",
        "read",
        "edit",
        "glob",
        "grep",
        "shell",
        "subagent",
        "skill",
        "question",
        "webfetch",
        "websearch",
        "external_directory",
        "mcp_tool",
    }
)


def normalize(value: str) -> str:
    return str(value or "").replace("\\", "/").lower()


def wildcard_match(pattern: str, value: str) -> bool:
    pat = normalize(pattern)
    val = normalize(value)
    if not pat:
        return False
    return fnmatch.fnmatchcase(val, pat)


@dataclass(frozen=True)
class PolicyStatement:
    action: str
    resource: str
    effect: PolicyEffect = "deny"
    source: str = "global"

    @property
    def priority(self) -> int:
        return SOURCE_PRIORITY.get(self.source, 0)

    def matches(self, action: str, resource: str) -> bool:
        return self.action in ("*", action) and wildcard_match(self.resource, resource)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    action: str
    resource: str
    matched: PolicyStatement | None = None

    @property
    def reason(self) -> str:
        if self.allowed:
            return "allowed"
        return "Blocked by configuration policy"


class PolicyDenied(PermissionError):
    """Raised when a hard policy blocks an action."""

    def __init__(self, decision: PolicyDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason)


def _parse(statements: Iterable[object], source: str) -> list[PolicyStatement]:
    out: list[PolicyStatement] = []
    for raw in statements:
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action") or "").strip().lower()
        resource = str(raw.get("resource") or "").strip()
        effect = str(raw.get("effect") or "").strip().lower()
        if not action or not resource or effect not in ("allow", "deny"):
            continue
        out.append(
            PolicyStatement(
                action=action,
                resource=resource,
                effect=effect,  # type: ignore[arg-type]
                source=str(raw.get("source") or source),
            )
        )
    return out


class PolicyEngine:
    """Ordered evaluate-only policy engine (no approval, no filesystem)."""

    def __init__(self, statements: Iterable[PolicyStatement] | None = None) -> None:
        self._statements: list[PolicyStatement] = list(statements or [])

    @classmethod
    def from_settings(cls) -> "PolicyEngine":
        return cls(_parse(getattr(get_settings(), "policies", []) or [], "global"))

    def with_statements(
        self, statements: Iterable[object] | Iterable[PolicyStatement], source: str
    ) -> "PolicyEngine":
        merged = list(self._statements)
        for item in statements:
            if isinstance(item, PolicyStatement):
                merged.append(item)
            else:
                merged.extend(_parse([item], source))
        # Stable sort by source priority; metadata order is preserved inside a source.
        merged.sort(key=lambda st: st.priority)
        return PolicyEngine(merged)

    def statements(self) -> list[PolicyStatement]:
        return list(self._statements)

    def evaluate(self, action: str, resource: str) -> PolicyDecision:
        act = str(action or "").strip().lower()
        res = str(resource or "").strip()
        matched: PolicyStatement | None = None
        allowed = True
        if act in ACTIONS or act == "*":
            for statement in self._statements:
                if statement.matches(act, res):
                    matched = statement
                    allowed = statement.effect == "allow"
        return PolicyDecision(allowed=allowed, action=act, resource=res, matched=matched)

    def check(self, action: str, resource: str) -> None:
        decision = self.evaluate(action, resource)
        if not decision.allowed:
            raise PolicyDenied(decision)


_ENGINE: PolicyEngine | None = None


def get_policy_engine() -> PolicyEngine:
    """Load policies from settings on each call so edits apply next turn."""
    return PolicyEngine.from_settings()


__all__ = [
    "ACTIONS",
    "PolicyDecision",
    "PolicyDenied",
    "PolicyEngine",
    "PolicyStatement",
    "SOURCE_PRIORITY",
    "get_policy_engine",
    "wildcard_match",
]
