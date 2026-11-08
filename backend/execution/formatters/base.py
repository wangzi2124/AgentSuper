"""Formatter contract for read snapshots and mutation checks (gap G-03)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Issue:
    message: str
    line: int = 0
    column: int = 0
    severity: str = "error"


@dataclass
class CheckResult:
    ok: bool = True
    issues: list[Issue] = field(default_factory=list)
    note: str = ""

    @classmethod
    def clean(cls, note: str = "") -> "CheckResult":
        return cls(ok=True, issues=[], note=note)

    @classmethod
    def failed(cls, message: str, *, line: int = 0, column: int = 0) -> "CheckResult":
        return cls(ok=False, issues=[Issue(message, line, column)])

    def summary(self) -> str:
        if self.ok:
            return self.note or "语法检查通过"
        first = self.issues[0] if self.issues else Issue("未知错误")
        location = ""
        if first.line:
            location = f"第 {first.line} 行"
            if first.column:
                location += f"第 {first.column} 列"
            location += "："
        return f"语法检查失败：{location}{first.message}"


class Formatter:
    """A language binding. ``check`` must never raise."""

    language = "text"

    def check(self, text: str) -> CheckResult:
        return CheckResult.clean("该类型不做语法检查")


__all__ = ["CheckResult", "Formatter", "Issue"]
