"""上下文压缩节点（对齐 agent.md §4.3）。

核心原则：宁可多留废话，不可丢精确值。
M1 提供「保留最近 + 摘要更早」的骨架；完整 9 段式压缩见 M2 的 `cognition/compressor.py`。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from langchain_core.messages import SystemMessage

logger = logging.getLogger(__name__)

try:  # pragma: no cover - 版本差异兜底
    from langchain_core.messages import RemoveMessage
    from langgraph.graph.message import REMOVE_ALL_MESSAGES
except Exception:  # noqa: BLE001
    RemoveMessage = None  # type: ignore
    REMOVE_ALL_MESSAGES = None  # type: ignore

KEEP_RECENT = 6


def _render(msg: Any) -> str:
    role = getattr(msg, "type", "user")
    content = getattr(msg, "content", "")
    return f"[{role}] {content}"


def make_compress_node(
    model: Any | None = None,
    keep_recent: int = KEEP_RECENT,
    compressor: Any | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    async def compress_node(state: dict[str, Any]) -> dict[str, Any]:
        messages = list(state.get("messages") or [])
        existing = state.get("context_summary", "")

        if compressor is not None:
            # 用 cognition 的阈值 + 保留最近 token 的增量压缩（§4.3）
            try:
                result = await compressor(messages, existing, model)
            except Exception as e:  # noqa: BLE001
                logger.warning("compressor failed, skip: %s", e)
                return {"token_usage": {"compressed": False}, "context_compressed": True}
            if not result.get("triggered"):
                return {"token_usage": {"compressed": False}, "context_compressed": True}
            summary = result.get("summary") or existing
            recent = result.get("recent") or messages
            payload: dict[str, Any] = {
                "context_summary": summary,
                "token_usage": {"compressed": True},
                "context_compressed": True,
            }
            if RemoveMessage is not None and REMOVE_ALL_MESSAGES is not None:
                payload["messages"] = [
                    RemoveMessage(id=REMOVE_ALL_MESSAGES),
                    SystemMessage(content=f"[历史摘要]\n{summary}"),
                    *recent,
                ]
            return payload

        if len(messages) <= keep_recent:
            return {"token_usage": {"compressed": True}, "context_compressed": True}

        older = messages[:-keep_recent]
        recent = messages[-keep_recent:]

        summary = existing
        # 门禁（agent-1.md §1.4）：只有 C1(≥5000 token) + C2(含精确值) + C3(本会话
        # LLM 摘要 < 上限) 全为真才调模型；否则退回规则截断，分文不花。
        from cognition.compressor import enforce_precision, should_use_llm_summary

        count = int(state.get("compress_count", 0) or 0)
        if model is not None and should_use_llm_summary(older, compress_count=count):
            try:
                rendered = "\n".join(_render(m) for m in older)
                prompt = (
                    "把以下较早对话压缩为结构化摘要，保留路径/命令/数字等精确值。\n"
                    f"已有摘要：{existing}\n\n{rendered}"
                )
                res = await model.ainvoke([{"role": "user", "content": prompt}])
                summary = res.content or existing
            except Exception:  # noqa: BLE001
                summary = existing or "(压缩调用失败，保留最近消息)"
            count += 1

        # 精确值回填（§1.5）：纯规则、零 LLM 成本、**每次压缩后**都执行（不只是 LLM
        # 分支）——走规则截断时 older 同样被丢弃，若不回填则路径/命令/数字一并丢失。
        summary = enforce_precision(summary, older)

        payload: dict[str, Any] = {
            "context_summary": summary,
            "compress_count": count,
            "token_usage": {"compressed": True},
            "context_compressed": True,
        }
        if RemoveMessage is not None and REMOVE_ALL_MESSAGES is not None:
            payload["messages"] = [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                SystemMessage(content=f"[历史摘要]\n{summary}"),
                *recent,
            ]
        return payload

    return compress_node
