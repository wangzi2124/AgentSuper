"""执行节点（对齐 agent.md §3.2 / §6.1）。

取出计划中下一个待办子任务，交给带工具的 LLM 循环执行；
结果写入 sub_results，并把任务标记为 done。
"""

from __future__ import annotations

from typing import Any, Callable

from execution.tool_registry import ToolRegistry
from models.router import TierModel

from .base import run_tool_loop

TOOL_SCOPE = "location"

VISION_SYSTEM_PREFIX = (
    "本回合用户消息中包含内联图片（image_url 分块），图片像素已真实随消息送达，你能直接看到它。\n"
    "- 直接依据图片内容作答，不要声称看不到图片、图片未提供或只有占位符。\n"
    "- 不要在工作区磁盘中搜索附件文件，附件是内联数据，不存在于文件系统。\n"
    "- 先给出对图像的直接观察，再回答用户问题。"
)


def _has_image_block(blocks: list[dict[str, Any]]) -> bool:
    return any(isinstance(b, dict) and b.get("type") == "image_url" for b in blocks)


def _multimodal_user_content(state: dict[str, Any]) -> list[dict[str, Any]] | None:
    """最近一条 human 消息的多模态 content（分块列表）；无图片则为 None。

    执行节点原本只把 `task.desc` 交给 LLM，用户原消息（含图片分块）会被丢弃 ——
    图片任务因此「看不见图」。这里把带图的原消息作为上下文补回。
    """
    for msg in reversed(state.get("messages") or []):
        if getattr(msg, "type", None) in ("human", "user"):
            content = getattr(msg, "content", "")
            return content if isinstance(content, list) else None
    return None


def make_execute_node(
    model: TierModel,
    registry: ToolRegistry,
    system_prompt: str = "",
    scope: str | None = TOOL_SCOPE,
) -> Callable[[dict[str, Any]], Any]:
    async def execute_node(state: dict[str, Any]) -> dict[str, Any]:
        plan = [dict(t) for t in (state.get("task_plan") or [])]
        pending = [t for t in plan if t.get("status") != "done"]
        if not pending:
            return {}

        task = pending[0]
        user_blocks = _multimodal_user_content(state)
        image_turn = bool(user_blocks) and _has_image_block(user_blocks)
        sys_text = system_prompt
        if image_turn:
            sys_text = f"{VISION_SYSTEM_PREFIX}\n\n{sys_text}" if sys_text else VISION_SYSTEM_PREFIX
        messages: list[dict[str, Any]] = []
        if sys_text:
            messages.append({"role": "system", "content": sys_text})
        if user_blocks is not None:
            messages.append({"role": "user", "content": user_blocks})
        messages.append({"role": "user", "content": task.get("desc", "")})

        result = await run_tool_loop(model, messages, registry, scope=scope)

        task_id = task.get("id", "t?")
        for t in plan:
            if t.get("id") == task_id:
                t["status"] = "done"
        sub_results = dict(state.get("sub_results") or {})
        sub_results[task_id] = result["text"]
        return {
            "task_plan": plan,
            "current_task_id": task_id,
            "sub_results": sub_results,
            "draft_output": result["text"],
            "tool_rounds": state.get("tool_rounds", 0) + result["rounds"],
            "token_usage": result.get("usage") or {},
        }

    return execute_node
