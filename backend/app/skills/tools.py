"""技能 → 模型工具 / 系统清单（对齐 opencode Skill 语义）。

之前技能只存在于「技能页 / API」，**没有进入主图工具表**，聊天里的模型既看不到
技能清单，也无法调用 —— 只能回答「没有这个技能 / 工作区是空的」。本模块补上：

- `make_skill_tools(loader)` → 每个已启用技能一个无参工具 `load_skill_<name>`，
  调用返回该技能 SKILL.md 正文（含工作流与输出规范）。
- `skills_catalog(loader)` → 系统提示里的技能清单（名称 + 描述 + 对应工具名），
  正文不常驻，按需加载，避免稀释注意力。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def safe_skill_tool_name(skill_name: str) -> str:
    """技能名 → 合法工具名（function name 仅允许 [a-zA-Z0-9_-]，长度 ≤64）。"""
    safe = "".join(
        ch if (ch.isalnum() or ch in "-_") else "-" for ch in (skill_name or "").strip()
    ).strip("-")
    return f"load_skill_{safe or 'unnamed'}"[:64]


def make_skill_tools(loader: Any) -> list[tuple[Callable[..., str], str, str]]:
    """返回 `[(fn, tool_name, description), ...]`，供 `ToolRegistry.register_function`。"""
    if loader is None:
        return []
    try:
        skills = loader.list()
    except Exception as exc:  # noqa: BLE001 - 技能源异常不应阻断建图
        logger.warning("skill tools skipped: %s", exc)
        return []

    tools: list[tuple[Callable[..., str], str, str]] = []
    used: set[str] = set()
    for skill in skills or []:
        if not getattr(skill, "enabled", True):
            continue
        if getattr(skill, "disable_model_invocation", False):
            continue
        name = safe_skill_tool_name(getattr(skill, "name", ""))
        if name in used:          # 技能名清洗后撞名：后者跳过
            continue
        used.add(name)
        skill_name = getattr(skill, "name", "")
        desc = (
            f"加载技能「{skill_name}」的完整说明与工作流。"
            f"适用场景：{getattr(skill, 'description', '') or '（无描述）'}"
        )

        def _load(_skill_name: str = skill_name) -> str:
            body = loader.get_skill_body(_skill_name)
            return body or f"（技能 {_skill_name} 内容为空）"

        _load.__name__ = name
        _load.__doc__ = desc
        tools.append((_load, name, desc))
    return tools


def skills_catalog(loader: Any, max_items: int = 80) -> str:
    """技能清单文本（注入 system prompt；正文按需 load）。"""
    if loader is None:
        return ""
    try:
        skills = loader.list()
    except Exception as exc:  # noqa: BLE001
        logger.warning("skills catalog skipped: %s", exc)
        return ""
    rows: list[str] = []
    for skill in skills or []:
        if not getattr(skill, "enabled", True):
            continue
        if getattr(skill, "disable_model_invocation", False):
            continue
        name = getattr(skill, "name", "")
        desc = (getattr(skill, "description", "") or "").strip().replace("\n", " ")
        rows.append(f"- {name}（调用 `{safe_skill_tool_name(name)}` 加载）：{desc}")
        if len(rows) >= max_items:
            break
    if not rows:
        return ""
    return (
        "## 可用技能（Skills）\n"
        "下面是本机已安装的技能。**当任务匹配某个技能时，先调用对应 `load_skill_*` "
        "工具加载其正文，再严格按正文的工作流执行**；不要凭记忆臆造技能内容。"
        "若用户消息以 `/技能名` 开头，该技能正文已由系统直接加载，无需再次调用工具。\n\n"
        + "\n".join(rows)
    )
