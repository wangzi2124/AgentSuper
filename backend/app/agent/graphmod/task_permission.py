"""[opencode task 授权] tool_task 委派权限规则。

对齐 opencode `permission.task` 语义：
  - 规则表按 subagent_type glob（支持 `*`/`?`）匹配，顺序求值，最后匹配者生效；
  - 值 ∈ {"allow", "ask", "deny"}，未匹配到规则时默认 "allow"（白名单内）。
  - deny → 工具 schema 裁剪（enum 移除）+ 运行时硬拒绝；
  - ask → 运行时走 permission_request 审批（前端弹窗），避免一次性授权；
  - allow → 直接放行。

规则来源：settings.task_permission_rules（.env `TASK_PERMISSION_RULES` JSON）。
空规则表 = 全 allow，行为与实现本模块之前完全一致（零迁移成本）。
"""

from __future__ import annotations

import fnmatch
import logging

logger = logging.getLogger(__name__)

_VALID_ACTIONS = ("allow", "ask", "deny")


def resolve(patterns: dict, subagent_type: str) -> str:
    """按顺序求值规则表，返回 subagent_type 得到的行为（allow/ask/deny）。

    Args:
        patterns: {pattern: action}，pattern 支持 glob（`*`/`?`/`explore*`），
            顺序求值、最后匹配者生效（对齐 opencode "last matching rule wins"）。
        subagent_type: 要委派的子 Agent 类型名。
    """
    action = _VALID_ACTIONS[0]
    if not patterns:
        return action
    for pattern, act in patterns.items():
        if not isinstance(pattern, str) or not isinstance(act, str):
            continue
        if act not in _VALID_ACTIONS:
            logger.warning("Invalid task permission action %r for pattern %r, ignored", act, pattern)
            continue
        if fnmatch.fnmatchcase(subagent_type, pattern):
            action = act
    return action


def allowed_subagent_types(subagent_type_names: list | tuple, patterns: dict) -> list:
    """按规则过滤可委派的子 Agent 类型（deny 的从 enum/白名单移除）。

    对齐 opencode "permission deny → 从 Task 工具描述移除"。
    """
    return [name for name in subagent_type_names if resolve(patterns, str(name)) != "deny"]