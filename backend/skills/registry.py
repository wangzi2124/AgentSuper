"""技能库（M5 · 规模化）。

最简技能注册表：按名称注册技能函数/元数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

@dataclass
class Skill:
    name: str
    description: str
    fn: Callable[..., Any]

class SkillRegistry:
    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill):
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list(self) -> list[str]:
        return sorted(self._skills)
