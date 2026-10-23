from __future__ import annotations

import logging
from typing import Any, Callable

from langchain_core.messages import SystemMessage

from cognition.compressor import estimate_tokens
from models.router import TIER_MAP, get_model

logger = logging.getLogger(__name__)


def _default_binding(target: str | None) -> dict[str, Any]:
    if not target:
        return {}
    if target in TIER_MAP:
        return {"tier": target, "model_name": TIER_MAP[target]}
    if "/" in target or target.startswith(("ollama/", "ollama_chat/")):
        return {"tier": "auto", "model_name": target}
    return {"tier": target, "model_name": TIER_MAP.get(target, target)}


def make_model_switch_node() -> Callable[[dict[str, Any]], dict[str, Any]]:
    async def model_switch_node(state: dict[str, Any]) -> dict[str, Any]:
        target = state.get("pending_model_switch")
        old = state.get("model_binding") or {}
        messages = list(state.get("messages") or [])
        existing_summary = state.get("context_summary") or ""
        iteration = int(state.get("iteration_count") or 0)

        summary = existing_summary
        try:
            total_tokens = sum(estimate_tokens(str(getattr(m, "content", m))) for m in messages)
        except Exception:
            total_tokens = 0

        if not summary and total_tokens > 20000 and messages:
            try:
                comp = get_model("simple", max_tokens=800)
                older = messages[:-6] if len(messages) > 6 else messages
                rendered = "\n".join(
                    f"[{getattr(m,'type','user')}] {getattr(m,'content','')}" for m in older
                )
                prompt = f"将以下历史压缩为简短交接摘要，保留路径、命令、数字、文件名。\n\n{rendered}"
                res = await comp.ainvoke([{"role": "user", "content": prompt}])
                if getattr(res, "content", None):
                    summary = str(res.content)
            except Exception as e:
                logger.debug("model_switch pre-compress skipped: %s", e)
                summary = existing_summary

        new_binding = _default_binding(target)
        handoff = SystemMessage(
            content=(
                "[模型交接] 会话中途切换模型\n"
                f"- 上一模型：{old.get('model_name') or old.get('tier') or '未知'}\n"
                f"- 新模型：{new_binding.get('model_name') or new_binding.get('tier')}\n"
                f"- 历史摘要：\n{summary or '（上下文较短，直接沿用近期消息）'}\n"
                "请基于上述信息继续当前任务，不要重复已完成的步骤。"
            )
        )

        log = list(state.get("model_switch_log") or [])
        log.append(
            {
                "from": old.get("model_name") or old.get("tier"),
                "to": new_binding.get("model_name") or new_binding.get("tier"),
                "at_iteration": iteration,
            }
        )

        return {
            "model_binding": new_binding,
            "model_switch_log": log,
            "pending_model_switch": None,
            "context_summary": summary or existing_summary,
            # 只追加交接消息；旧实现把 [handoff, *messages] 交回 add_messages，
            # 会重复写入整段历史（消息翻倍、ID 冲突）。
            "messages": [handoff],
        }

    return model_switch_node
