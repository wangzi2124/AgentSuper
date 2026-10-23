"""与模型无关的工具执行 Hook（agent-4 §4 / §6-P2）。

用法::

    token = set_tool_hooks([hook])
    try:
        ...
    finally:
        reset_tool_hooks(token)

``pre`` 返回新 args（None 表示不改）；``post`` 返回清洗后的输出（None 表示不改）。
Hook 抛异常视为不拦截，绝不影响工具循环。
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

PreTool = Callable[[str, dict[str, Any]], "dict[str, Any] | None"]
PostTool = Callable[[str, dict[str, Any], str], "str | None"]


@dataclass
class ToolHook:
    pre: PreTool | None = None
    post: PostTool | None = None


_HOOKS: contextvars.ContextVar[tuple[ToolHook, ...]] = contextvars.ContextVar(
    "harness_tool_hooks", default=()
)


def set_tool_hooks(hooks: list[ToolHook] | tuple[ToolHook, ...]) -> contextvars.Token:
    return _HOOKS.set(tuple(hooks or ()))


def reset_tool_hooks(token: contextvars.Token) -> None:
    _HOOKS.reset(token)


def run_pre_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    current = args
    for hook in _HOOKS.get():
        if hook.pre is None:
            continue
        try:
            updated = hook.pre(name, current)
        except Exception:  # noqa: BLE001
            logger.warning("harness pre-tool hook 失败（%s），已跳过", name, exc_info=True)
            continue
        if isinstance(updated, dict):
            current = updated
    return current


def run_post_tool(name: str, args: dict[str, Any], output: str) -> str:
    current = output
    for hook in _HOOKS.get():
        if hook.post is None:
            continue
        try:
            updated = hook.post(name, args, current)
        except Exception:  # noqa: BLE001
            logger.warning("harness post-tool hook 失败（%s），已跳过", name, exc_info=True)
            continue
        if isinstance(updated, str):
            current = updated
    return current
