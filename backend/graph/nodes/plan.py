"""任务分解节点（对齐 agent.md §3.2 / §7.5.1）。

无模型时（M1 快速路径）：把用户请求转成单步计划。
注入 `plan_model` 时（M3）：用 LLM 分解为结构化子任务（含 agent 归属），
供并行扇出与委派使用。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from prompts.loader import PromptLoader

from ..tasks import KNOWN_AGENTS, parse_task_specs
from ..usage import usage_from_result
from .base import content_text

logger = logging.getLogger(__name__)

MAX_TASKS = 5

# 纯寒暄/致谢/应答识别（全串锚定，正文带任何任务内容即不命中）——
# 这类消息若进工具循环，弱本地模型会臆造文件名并把「文件不存在」错误
# 当成最终答案（实测 qwen2.5:3b 对「你好」返回「错误：文件不存在 工作区.txt」）。
_GREETING_RE = re.compile(
    r"^(?:你好+|您好|嗨+|哈喽|哈啰?|hi+|hello|hey|"
    r"在吗|在么|在不在|有人吗|有人么|请问在吗|"
    r"早上好|早安|上午好|中午好|下午好|傍晚好|晚上好|晚安)"
    r"[!！~～。.，,\s?？]*$",
    re.IGNORECASE,
)
_THANKS_RE = re.compile(
    r"^(?:谢谢+|谢谢你|感谢你?|多谢+|谢啦|thanks?|thx|thank u)"
    r"[!！~～。.，,\s?？]*$",
    re.IGNORECASE,
)
_ACK_RE = re.compile(
    r"^(?:好的+|okay|ok|嗯+|哦+|噢+|收到了?|明白|了解|知道了?)"
    r"[!！~～。.，,\s?？]*$",
    re.IGNORECASE,
)

SMALLTALK_REPLIES = {
    "greeting": (
        "你好！我是工作区助手，可以帮你读取、新建、编辑、搜索工作区文件，"
        "也能执行 shell 命令。直接告诉我需求即可，比如「列出目录」或「读取 xxx.md」。"
    ),
    "thanks": "不客气！还有其它需要随时告诉我。",
    "ack": "好的，需要时随时找我。",
}


def classify_smalltalk(text: str) -> str | None:
    """纯寒暄返回 'greeting'/'thanks'/'ack'；含任务内容或为空返回 None。"""
    s = (text or "").strip()
    if _GREETING_RE.match(s):
        return "greeting"
    if _THANKS_RE.match(s):
        return "thanks"
    if _ACK_RE.match(s):
        return "ack"
    return None


def _latest_human_has_blocks(state: dict[str, Any]) -> bool:
    """最近一条 human 消息是否为分块 content（带图片等附件时不做寒暄短路）。"""
    for msg in reversed(state.get("messages") or []):
        if getattr(msg, "type", None) in ("human", "user"):
            return isinstance(getattr(msg, "content", ""), list)
    return False


def _last_user_text(state: dict[str, Any]) -> str:
    for msg in reversed(state.get("messages") or []):
        role = getattr(msg, "type", None)
        if role in ("human", "user"):
            return content_text(getattr(msg, "content", ""))
    messages = state.get("messages") or []
    if messages:
        return content_text(getattr(messages[-1], "content", ""))
    return ""


def _single(text: str) -> list[dict[str, Any]]:
    return [{"id": "t1", "desc": text or "完成用户请求", "status": "pending"}]


def make_plan_node(
    model: Any | None = None,
    max_tasks: int = MAX_TASKS,
) -> Callable[[dict[str, Any]], Any]:
    async def plan_node(state: dict[str, Any]) -> dict[str, Any]:
        if state.get("task_plan"):
            return {}

        request = _last_user_text(state)

        if not _latest_human_has_blocks(state):
            kind = classify_smalltalk(request)
            if kind:
                # 短路：任务直接标记完成，草稿即回复 —— 不进工具循环、不调 LLM。
                # 路由：route_after_plan（无待办）→ execute 空转 → supervisor →
                # synthesize（sub_results 为空 → 采用 draft_output）→ END。
                return {
                    "task_plan": [{"id": "t1", "desc": request, "status": "done"}],
                    "draft_output": SMALLTALK_REPLIES[kind],
                }

        if model is None:
            return {"task_plan": _single(request)}

        usage: dict[str, Any] = {}
        try:
            prompt = PromptLoader().load("nodes/plan.md", content=request)
            res = await model.ainvoke([{"role": "user", "content": prompt}])
            usage = usage_from_result(res, getattr(model, "tier", None))
            specs = parse_task_specs(getattr(res, "content", "") or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("plan decomposition failed, using single task: %s", exc)
            specs = []

        if not specs:
            return {"task_plan": _single(request), "token_usage": usage}

        tasks = [
            {
                "id": s.id,
                "desc": s.objective,
                "agent": s.agent,
                "context": s.context,
                "deliverable": s.deliverable,
                "status": "pending",
            }
            for s in specs[:max_tasks]
            if s.agent in KNOWN_AGENTS
        ]
        if not tasks:
            return {"task_plan": _single(request), "token_usage": usage}
        return {"task_plan": tasks, "token_usage": usage}

    return plan_node
