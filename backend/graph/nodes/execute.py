"""执行节点（对齐 agent.md §3.2 / §6.1）。

取出计划中下一个待办子任务，交给带工具的 LLM 循环执行；
结果写入 sub_results，并把任务标记为 done。
"""

from __future__ import annotations

from typing import Any, Callable

from execution.tool_registry import ToolRegistry
from harness.budget import BudgetLedger
from harness.context import assemble_model_view
from models.router import TierModel, resolve_model

from harness.tool_history import human_count, last_human_id, merge_tool_history
from .base import content_text, run_tool_loop

TOOL_SCOPE = "location"

VISION_SYSTEM_PREFIX = (
    "本回合用户消息中包含内联图片（image_url 分块），图片像素已真实随消息送达，你能直接看到它。\n"
    "- 直接依据图片内容作答，不要声称看不到图片、图片未提供或只有占位符。\n"
    "- 不要在工作区磁盘中搜索附件文件，附件是内联数据，不存在于文件系统。\n"
    "- 先给出对图像的直接观察，再回答用户问题。"
)

# 历史注入预算（字符）：把同一 thread 的既有对话（checkpointer 中的 state.messages）
# 作为上下文喂给 LLM，使模型具备会话内短期记忆（对齐 agent.md §4.2：工作记忆=State.messages）。
# 只保留最近的内容，超出预算从最旧处丢弃，避免提示词无限增长。
_HISTORY_BUDGET_CHARS = 12_000

# 历史里不需要回灌的 role（工具中间结果属噪音；压缩摘要另以 system 保留）。



def _has_image_block(blocks: list[dict[str, Any]]) -> bool:
    return any(isinstance(b, dict) and b.get("type") == "image_url" for b in blocks)


def _conversation_history(
    state: dict[str, Any], budget_chars: int | None = None
) -> list[dict[str, Any]]:
    """Assemble the model view via ContextHarness (summary + tail + tool history)."""
    return assemble_model_view(
        state,
        content_text=content_text,
        budget_chars=budget_chars,
        history_budget_chars=_HISTORY_BUDGET_CHARS,
    )


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
    model: TierModel | None = None,
    registry: ToolRegistry | None = None,
    system_prompt: str = "",
    scope: str | None = TOOL_SCOPE,
) -> Callable[[dict[str, Any]], Any]:
    from models.router import resolve_model

    _model = model
    _registry = registry
    _system_prompt = system_prompt

    async def execute_node(state: dict[str, Any]) -> dict[str, Any]:
        model = _model if _model is not None else resolve_model(state, default_tier="medium")
        registry = _registry
        system_prompt = _system_prompt
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
        # 长期记忆（Store，§4.2）由 memory_recall 节点写入 memory_context，此前无人消费；
        # 这里并入 system 段落，让跨会话事实也能进入本轮提示词。
        memory_ctx = (state.get("memory_context") or "").strip()
        if memory_ctx:
            sys_text = (
                f"{sys_text}\n\n[相关长期记忆]\n{memory_ctx}" if sys_text else f"[相关长期记忆]\n{memory_ctx}"
            )
        # 知识库检索结果（§4.1）由 rag 节点写入 rag_context，此前无人消费 →
        # 结果只以 SystemMessage 落进 messages、不进提示词。这里并入 system 段落，
        # 让 RAG 命中的文档片段真正进入本轮上下文。
        rag_ctx = (state.get("rag_context") or "").strip()
        if rag_ctx:
            sys_text = (
                f"{sys_text}\n\n[知识库检索结果]\n{rag_ctx}"
                if sys_text
                else f"[知识库检索结果]\n{rag_ctx}"
            )
        messages: list[dict[str, Any]] = []
        if sys_text:
            messages.append({"role": "system", "content": sys_text})
        # 会话内短期记忆（工作记忆，§4.2）：回灌同一 thread 的既有对话，使模型记得
        # 前几轮说过的内容（此前 execute 只送 task.desc → 追问「我上次说的代号是什么」
        # 完全失忆）。
        messages.extend(_conversation_history(state))
        if user_blocks is not None:
            messages.append({"role": "user", "content": user_blocks})
        messages.append({"role": "user", "content": task.get("desc", "")})

        result = await run_tool_loop(
            model,
            messages,
            registry,
            scope=scope,
            agent_id="build",
            budget=BudgetLedger.from_state(state),
            stream=True,
        )

        tool_updates: dict[str, Any] = {}
        new_tool_history = result.get("tool_history") or []
        if new_tool_history:
            raw_messages = state.get("messages") or []
            merged, turn, anchor = merge_tool_history(
                state.get("tool_history") or [],
                new_tool_history,
                existing_turn=state.get("tool_history_turn"),
                new_turn=human_count(raw_messages),
                existing_anchor=state.get("tool_history_anchor_id"),
                new_anchor=last_human_id(raw_messages),
            )
            tool_updates = {
                "tool_history": merged,
                "tool_history_turn": turn,
                "tool_history_anchor_id": anchor,
            }

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
            **tool_updates,
        }

    return execute_node
