"""slash commands"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

_SLASH_RE = re.compile(r"^\s*/([\w\-\u4e00-\u9fff]+)\s*(.*)$", re.DOTALL)
_EMPTY_HINT_SKILL = "（用户只输入了技能命令，未附带具体请求。请先按该技能的工作流，向用户确认必要信息。）"
_EMPTY_HINT_PLUGIN = "（用户只输入了插件命令，未附带具体请求。请根据插件说明，协助用户合理使用该插件提供的能力。）"


def parse_slash_command(question: str) -> Optional[str]:
    m = _SLASH_RE.match(question or "")
    return m.group(1) if m else None


def _get_plugin_body(plugin: Any) -> str:
    parts = []
    name = getattr(plugin, "name", "") or ""
    desc = getattr(plugin, "description", "") or ""
    version = getattr(plugin, "version", "") or ""
    if name:
        header = "插件：" + str(name)
        if version:
            header = header + " (v" + str(version) + ")"
        parts.append(header)
    if desc:
        parts.append("说明：" + str(desc))
    funcs = getattr(plugin, "functions", None)
    if funcs:
        try:
            if isinstance(funcs, dict):
                names = list(funcs.keys())
            elif isinstance(funcs, (list, set, tuple)):
                names = list(funcs)
            else:
                names = []
            if names:
                parts.append("可用工具函数：" + ", ".join(sorted([str(x) for x in names])))
        except Exception:
            pass
    body = "\n".join([p for p in parts if p and str(p).strip()])
    return body


def apply_slash_skill(question: str, loader: Any = None, plugin_loader: Any = None, *args, **kwargs) -> Tuple[str, Optional[str]]:
    if plugin_loader is None and len(args) > 0:
        plugin_loader = args[0]
    if plugin_loader is None:
        plugin_loader = kwargs.get("plugin_loader")

    m = _SLASH_RE.match(question or "")
    if not m:
        return question, None
    name = m.group(1)
    rest = (m.group(2) or "").strip()

    if loader is not None:
        try:
            skill = loader.get(name)
        except Exception as exc:
            logger.warning("slash skill lookup failed: %s", exc)
            skill = None
        if skill is not None and getattr(skill, "enabled", True):
            try:
                body = loader.get_skill_body(name) or ""
            except Exception as exc:
                logger.warning("slash skill body failed: %s", exc)
                body = ""
            if body.strip():
                out = rest or _EMPTY_HINT_SKILL
                injected = "[技能指令：" + name + "]\n" + body.strip() + "\n[技能指令结束]\n\n用户请求：\n" + out
                return injected, name

    if plugin_loader is not None:
        try:
            plugin = plugin_loader.get(name)
        except Exception as exc:
            logger.warning("slash plugin lookup failed: %s", exc)
            plugin = None
        if plugin is not None and getattr(plugin, "enabled", True):
            body = _get_plugin_body(plugin)
            if body.strip():
                out = rest or _EMPTY_HINT_PLUGIN
                injected = "[插件指令：" + name + "]\n" + body.strip() + "\n[插件指令结束]\n\n用户请求：\n" + out
                return injected, name

    return question, None
