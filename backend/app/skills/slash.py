"""斜杠技能命令（`/docx ...`）→ 本轮强制加载技能正文。

只做**文本层**处理，不依赖模型是否记得调用 `load_skill_*`：
- 消息以 `/名称` 开头且该名称是**已安装且启用**的技能时，把 SKILL.md 正文以
  清晰分隔块插到用户消息前面，并从消息里剥掉命令前缀；
- 名称不存在/未启用 → 原样返回（避免把 `/etc/hosts` 这类路径误当命令）。

返回 `(new_question, skill_name|None)`；`skill_name` 供上层做「本技能已加载」提示。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

# `/名称` + 余下内容（跨行）；名称允许中英文、数字、下划线、连字符。
_SLASH_RE = re.compile(r"^\s*/([\w\-\u4e00-\u9fff]+)\s*([\s\S]*)$", re.UNICODE)

_EMPTY_HINT = "（用户只输入了技能命令，未附带具体请求。请先按该技能的工作流，向用户确认必要信息。）"


def parse_slash_command(question: str) -> Optional[str]:
    """提取 `/名称` 里的名称；不是斜杠命令返回 None。"""
    m = _SLASH_RE.match(question or "")
    return m.group(1) if m else None


def apply_slash_skill(
    question: str,
    loader: Any,
) -> Tuple[str, Optional[str]]:
    """若首行是已安装技能命令 → 返回注入正文后的消息与技能名。"""
    name = parse_slash_command(question)
    if not name or loader is None:
        return question, None
    try:
        skill = loader.get(name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("slash skill lookup failed: %s", exc)
        return question, None
    if skill is None or not getattr(skill, "enabled", True):
        return question, None
    try:
        body = loader.get_skill_body(name) or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("slash skill body failed: %s", exc)
        return question, None
    if not body.strip():
        return question, None

    m = _SLASH_RE.match(question or "")
    rest = (m.group(2) if m else "").strip() or _EMPTY_HINT
    injected = (
        f"[技能指令：{name}]\n"
        f"{body.strip()}\n"
        f"[技能指令结束]\n\n"
        f"用户请求：\n{rest}"
    )
    return injected, name
