"""执行节点（对齐 agent.md §3.2 / §6.1）。

取出计划中下一个待办子任务，交给带工具的 LLM 循环执行；
结果写入 sub_results，并把任务标记为 done。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from execution.tool_registry import ToolRegistry
from harness.budget import BudgetLedger
from models.router import TierModel, resolve_model
from prompts.assembly import compose_system
from prompts.loader import model_id_of

from .base import build_tool_defs, run_tool_loop

TOOL_SCOPE = "location"

VISION_SYSTEM_PREFIX = (
    "本回合用户消息中包含内联图片（image_url 分块），图片像素已真实随消息送达，你能直接看到它。\n"
    "- 直接依据图片内容作答，不要声称看不到图片、图片未提供或只有占位符。\n"
    "- 不要在工作区磁盘中搜索附件文件，附件是内联数据，不存在于文件系统。\n"
    "- 先给出对图像的直接观察，再回答用户问题。"
)


def _has_image_block(blocks: list[dict[str, Any]]) -> bool:
    return any(isinstance(b, dict) and b.get("type") == "image_url" for b in blocks)


def _msg_to_provider_dict(msg: Any) -> dict[str, Any] | None:
    """把 LangChain 消息还原成 provider 结构（保留原生 tool_calls / tool 结果）。"""
    role = getattr(msg, "type", None) or getattr(msg, "role", None) or ""
    content = getattr(msg, "content", "")
    ident = getattr(msg, "id", None)
    if role in ("human", "user"):
        out = {"role": "user", "content": content}
        if ident:
            out["_id"] = str(ident)
        return out
    if role in ("ai", "assistant"):
        out: dict[str, Any] = {"role": "assistant", "content": content}
        if ident:
            out["_id"] = str(ident)
        calls = getattr(msg, "tool_calls", None) or []
        if calls:
            out["tool_calls"] = [
                {
                    "id": c.get("id"),
                    "type": "function",
                    "function": {
                        "name": c.get("name"),
                        "arguments": c.get("args")
                        if isinstance(c.get("args"), str)
                        else json.dumps(c.get("args") or {}, ensure_ascii=False),
                    },
                }
                for c in calls
                if isinstance(c, dict)
            ]
        return out
    if role == "tool":
        out = {
            "role": "tool",
            "content": content,
            "tool_call_id": getattr(msg, "tool_call_id", ""),
            "name": getattr(msg, "name", "") or "",
        }
        if ident:
            out["_id"] = str(ident)
        return out
    if role == "system":
        out = {"role": "system", "content": content}
        if ident:
            out["_id"] = str(ident)
        return out
    return None


def _tool_history_to_lc(history: list[dict[str, Any]]) -> list[Any]:
    """把工具轮还原成原生 LangChain 消息，写回 state 供下一轮重建。"""
    from langchain_core.messages import AIMessage, ToolMessage

    out: list[Any] = []
    for msg in history:
        role = msg.get("role")
        if role == "assistant":
            calls = []
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                raw = fn.get("arguments")
                try:
                    args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except Exception:  # noqa: BLE001
                    args = {}
                calls.append(
                    {
                        "id": call.get("id"),
                        "name": fn.get("name") or call.get("name"),
                        "args": args if isinstance(args, dict) else {},
                        "type": "tool_call",
                    }
                )
            out.append(
                AIMessage(content=str(msg.get("content") or ""), tool_calls=calls)
            )
        elif role == "tool":
            out.append(
                ToolMessage(
                    content=str(msg.get("content") or ""),
                    tool_call_id=str(msg.get("tool_call_id") or ""),
                    name=str(msg.get("name") or ""),
                )
            )
    return out


def _conversation_history(
    state: dict[str, Any], budget_chars: int | None = None
) -> list[dict[str, Any]]:
    """直接使用本轮已由 session.db 重建的 state.messages（含摘要 + tail）。"""
    raw = list(state.get("messages") or [])
    cut = len(raw)
    for i in range(len(raw) - 1, -1, -1):
        role = getattr(raw[i], "type", None) or getattr(raw[i], "role", None)
        if role in ("human", "user"):
            cut = i
            break
    summary = str(state.get("context_summary") or "").strip()
    out: list[dict[str, Any]] = []
    for msg in raw[:cut]:
        item = _msg_to_provider_dict(msg)
        if item is None:
            continue
        if (
            summary
            and item["role"] == "system"
            and str(item.get("content") or "").startswith("[历史摘要]")
        ):
            continue
        out.append(item)
    if summary:
        out.insert(0, {"role": "system", "content": f"[历史摘要]\n{summary}"})
    if budget_chars is not None and budget_chars > 0:
        systems = [m for m in out if m["role"] == "system"]
        convo = [m for m in out if m["role"] != "system"]
        total = sum(len(str(m.get("content") or "")) for m in convo)
        while convo and total > budget_chars:
            total -= len(str(convo[0].get("content") or ""))
            convo.pop(0)
        out = systems + convo
    return out


def _current_user_id(state: dict[str, Any]) -> str | None:
    for msg in reversed(state.get("messages") or []):
        if getattr(msg, "type", None) in ("human", "user"):
            value = getattr(msg, "id", None)
            return str(value) if value else None
    return None


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
    system_prompt: str | None = None,
    scope: str | None = TOOL_SCOPE,
    extra_system: str = "",
) -> Callable[[dict[str, Any]], Any]:
    from models.router import resolve_model

    _model = model
    _registry = registry
    _system_prompt = system_prompt
    _extra_system = extra_system

    async def execute_node(state: dict[str, Any]) -> dict[str, Any]:
        agent_def = None
        try:
            from agents.registry import load_registry
            from agents.resolver import AgentResolver

            agent_def = AgentResolver(
                load_registry(state.get("workspace") or None)
            ).primary(str(state.get("agent") or "") or None)
        except Exception:  # noqa: BLE001 - agent resolution is advisory
            agent_def = None
        agent_id = agent_def.name if agent_def is not None else "build"
        allowed_tools = set(agent_def.tools) if agent_def is not None and agent_def.tools else None

        model = _model
        if model is None and state.get("model_override"):
            model = TierModel(tier="auto", model_override=str(state.get("model_override")))
        if model is None and agent_def is not None and agent_def.model:
            model = TierModel(tier=agent_def.tier, model_override=agent_def.model)
        if model is None:
            model = resolve_model(state, default_tier="medium")
        registry = _registry
        plan = [dict(t) for t in (state.get("task_plan") or [])]
        pending = [t for t in plan if t.get("status") != "done"]
        if not pending:
            return {}

        task = pending[0]
        user_blocks = _multimodal_user_content(state)
        image_turn = bool(user_blocks) and _has_image_block(user_blocks)
        extra_system = _extra_system
        if agent_def is not None and agent_def.prompt_body.strip():
            extra_system = f"{extra_system}\n\n{agent_def.prompt_body.strip()}".strip()
        # Stable head first; vision/memory/RAG/skills are turn-specific and stay
        # after it so provider prefix caching is not invalidated by volatile text.
        sys_text = compose_system(
            "file_agent",
            model_id_of(model),
            head_override=_system_prompt,
            vision_prefix=VISION_SYSTEM_PREFIX if image_turn else "",
            memory=(state.get("memory_context") or "").strip(),
            rag=(state.get("rag_context") or "").strip(),
            extra=extra_system,
            instructions=(state.get("instructions") or "").strip(),
        )
        tool_defs = build_tool_defs(registry, scope) if registry is not None else []
        if allowed_tools is not None:
            tool_defs = [
                spec
                for spec in tool_defs
                if spec.get("function", {}).get("name") in allowed_tools
            ]
        try:
            from cognition.context_meter import get_meter

            context_estimate = get_meter().estimate(
                [],
                system=sys_text,
                tools=tool_defs or None,
                model_id=model_id_of(model) or "",
            )
            context_system_tokens = int(context_estimate.system)
            context_tool_tokens = int(context_estimate.tools)
        except Exception:  # noqa: BLE001 - accounting must not break execute
            context_system_tokens = 0
            context_tool_tokens = 0
        messages: list[dict[str, Any]] = []
        if sys_text:
            messages.append({"role": "system", "content": sys_text})
        # 会话内短期记忆（工作记忆，§4.2）：回灌同一 thread 的既有对话，使模型记得
        # 前几轮说过的内容（此前 execute 只送 task.desc → 追问「我上次说的代号是什么」
        # 完全失忆）。
        messages.extend(_conversation_history(state))
        current_user_id = _current_user_id(state)
        if user_blocks is not None:
            item = {"role": "user", "content": user_blocks}
            if current_user_id:
                item["_id"] = current_user_id
            messages.append(item)
        # 当前轮 human 消息被历史装配排除；附件正文需单独补回，否则模型看不到文件内容。
        attachment_ctx = str(state.get("attachment_context") or "").strip()
        if attachment_ctx:
            messages.append({"role": "user", "content": attachment_ctx})
        task_user: dict[str, Any] = {"role": "user", "content": task.get("desc", "")}
        if current_user_id:
            task_user["_id"] = current_user_id
        messages.append(task_user)

        compaction_events: list[dict[str, Any]] = []

        async def _on_compaction(event: dict[str, Any]) -> None:
            compaction_events.append(event)

        from execution.access import reset_agent_policy, set_agent_policy, statements_from_permission

        policy_token = set_agent_policy(
            statements_from_permission(agent_def.permission if agent_def is not None else None)
        )
        try:
            result = await run_tool_loop(
                model,
                messages,
                registry,
                scope=scope,
                agent_id=agent_id,
                budget=BudgetLedger.from_state(state),
                stream=True,
                context_usable_tokens=int(state.get("compress_threshold") or 0),
                on_compaction=_on_compaction,
                session_id=str(state.get("session_id") or ""),
                allowed_tools=allowed_tools,
            )
        finally:
            reset_agent_policy(policy_token)

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
            "messages": _tool_history_to_lc(result.get("tool_history") or []),
            "compaction_event": compaction_events[-1] if compaction_events else {},
            "context_system_tokens": context_system_tokens,
            "context_tool_tokens": context_tool_tokens,
        }

    return execute_node
