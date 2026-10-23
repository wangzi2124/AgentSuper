"""输入过滤节点（对齐 agent.md §3.2 前置闸门）。

在调用任何模型**之前**拦掉「无意义输入」——纯语气词/问候语/叹词/致谢/应答
（如「你好」「谢谢」「嗯」「ok」）以及探活测试输入。命中即用**固定模板**作答
（工具介绍 + 引导），既不调用 LLM、也不进入工具循环，显著省时省 token；
未命中则原样放行（返回 `{}`，由 supervisor 继续正常编排）。

判定口径复用 `plan.classify_smalltalk`（全串锚定：正文带任何任务内容即不命中，
避免「你好，帮我读文件」被误拦）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .plan import SELF_INTRO_MAIN, classify_smalltalk

logger = logging.getLogger(__name__)

# 固定模板（系统介绍）：命中无意义输入时直接返回，**不调用模型**。
TOOL_INTRO_TEMPLATE = SELF_INTRO_MAIN


def _last_human_msg(state: dict[str, Any]):
    for msg in reversed(state.get("messages") or []):
        if getattr(msg, "type", None) in ("human", "user"):
            return msg
    return None


def make_filter_node() -> Callable[[dict[str, Any]], dict[str, Any]]:
    def filter_node(state: dict[str, Any]) -> dict[str, Any]:
        # 已有计划/最终结果时不介入（幂等：仅在本轮首过、尚无计划时判定）。
        if state.get("task_plan") or state.get("final_output"):
            return {}

        msg = _last_human_msg(state)
        if msg is None:
            return {}
        content = getattr(msg, "content", "")
        # 带附件（图片等分块 content）的本回合不短路，交给正常链路处理。
        if isinstance(content, list):
            return {}
        text = content if isinstance(content, str) else ""
        kind = classify_smalltalk(text)
        if not kind:
            return {}

        logger.info("input filter: filler message (%s) → fixed template, no model call", kind)
        # 固定模板直接作答：写入 draft_output + final_output，路由（route_after_supervisor
        # 见 final_output → end）直接收尾；不进工具循环、不调用任何模型。
        return {
            "task_plan": [{"id": "t1", "desc": text, "status": "done"}],
            "draft_output": TOOL_INTRO_TEMPLATE,
            "final_output": TOOL_INTRO_TEMPLATE,
        }

    return filter_node
