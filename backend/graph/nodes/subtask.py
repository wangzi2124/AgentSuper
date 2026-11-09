"""Isolated subtask execution for slash commands with ``subtask: true``.

A subtask command runs in its own context: the model sees the selected agent's
system prompt plus the rendered command text, **not** the session's prior
conversation. Tool access is scoped to the agent, and the result is written back
as this turn's final output so the normal projector/streaming paths still apply.
"""

from __future__ import annotations

from typing import Any, Callable

from execution.tool_registry import ToolRegistry
from harness.budget import BudgetLedger
from models.router import TierModel, resolve_model
from prompts.assembly import compose_system
from prompts.loader import model_id_of

from .base import build_tool_defs, run_tool_loop
from .execute import (
    TOOL_SCOPE,
    VISION_SYSTEM_PREFIX,
    _has_image_block,
    _multimodal_user_content,
    _tool_history_to_lc,
)


def _last_user_content(state: dict[str, Any]) -> Any:
    for msg in reversed(state.get("messages") or []):
        if getattr(msg, "type", None) in ("human", "user"):
            return getattr(msg, "content", "")
    return ""


def make_subtask_node(
    model: TierModel | None = None,
    registry: ToolRegistry | None = None,
    system_prompt: str | None = None,
    scope: str | None = TOOL_SCOPE,
    extra_system: str = "",
) -> Callable[[dict[str, Any]], Any]:
    _model = model
    _registry = registry
    _system_prompt = system_prompt
    _extra_system = extra_system

    async def subtask_node(state: dict[str, Any]) -> dict[str, Any]:
        agent_def = None
        try:
            from agents.registry import load_registry
            from agents.resolver import AgentResolver

            agent_def = AgentResolver(
                load_registry(state.get("workspace") or None)
            ).resolve(str(state.get("agent") or "") or None)
        except Exception:  # noqa: BLE001 - agent resolution is advisory
            agent_def = None
        agent_id = agent_def.name if agent_def is not None else "build"

        from agents.registry import request_kwargs

        request_params = (
            request_kwargs(agent_def.request) if agent_def is not None else None
        )
        loop_kwargs: dict[str, Any] = {}
        if agent_def is not None and agent_def.max_rounds > 0:
            loop_kwargs["max_rounds"] = agent_def.max_rounds
        if request_params:
            loop_kwargs["request_params"] = request_params

        model = _model
        if model is None and state.get("model_override"):
            model = TierModel(
                tier="auto", model_override=str(state.get("model_override"))
            )
        if model is None and agent_def is not None and agent_def.model:
            model = TierModel(tier=agent_def.tier, model_override=agent_def.model)
        if model is None:
            model = resolve_model(state, default_tier="medium")

        registry = _registry
        allowed_tools = (
            set(agent_def.tools)
            if agent_def is not None and agent_def.tools
            else None
        )

        user_blocks = _multimodal_user_content(state)
        image_turn = bool(user_blocks) and _has_image_block(user_blocks)
        extra_system = _extra_system
        if agent_def is not None and agent_def.prompt_body.strip():
            extra_system = f"{extra_system}\n\n{agent_def.prompt_body.strip()}".strip()
        sys_text = compose_system(
            "file_agent",
            model_id_of(model),
            head_override=_system_prompt,
            vision_prefix=VISION_SYSTEM_PREFIX if image_turn else "",
            memory="",
            rag="",
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

        # Isolation: system + current command text only. No history, summary, RAG
        # or memory from the parent session.
        messages: list[dict[str, Any]] = []
        if sys_text:
            messages.append({"role": "system", "content": sys_text})
        attachment_ctx = str(state.get("attachment_context") or "").strip()
        if attachment_ctx:
            messages.append({"role": "user", "content": attachment_ctx})
        current = user_blocks if user_blocks is not None else _last_user_content(state)
        messages.append({"role": "user", "content": current})

        compaction_events: list[dict[str, Any]] = []

        async def _on_compaction(event: dict[str, Any]) -> None:
            compaction_events.append(event)

        from execution.access import (
            reset_agent_policy,
            set_agent_policy,
            statements_from_permission,
        )

        policy_token = set_agent_policy(
            statements_from_permission(
                agent_def.permission if agent_def is not None else None
            )
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
                **loop_kwargs,
            )
        finally:
            reset_agent_policy(policy_token)

        text = result["text"]
        return {
            "task_plan": [{"id": "subtask", "desc": "subtask", "status": "done"}],
            "current_task_id": "subtask",
            "sub_results": {"subtask": text},
            "draft_output": text,
            "final_output": text,
            "tool_rounds": state.get("tool_rounds", 0) + result["rounds"],
            "token_usage": result.get("usage") or {},
            "messages": _tool_history_to_lc(result.get("tool_history") or []),
            "compaction_event": compaction_events[-1] if compaction_events else {},
            "context_system_tokens": context_system_tokens,
            "context_tool_tokens": context_tool_tokens,
        }

    return subtask_node


__all__ = ["make_subtask_node"]
