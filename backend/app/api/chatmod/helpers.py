"""拆分模块 `helpers`（含 MAX_HISTORY_TOKENS、MAX_MESSAGE_LENGTH、_ALLOWED_HISTORY_KEYS、_DEFAULT_USER_ID、_generate_title、_get_session_service、_get_summarizer、_get_user_id、_msg_type_to_role、_sanitize_history、_summarizer、_summarizer_model、_truncate_history、_validate_chat_message、reset_summarizer）。

原文件 docstring: 聊天 API 路由模块。

提供聊天对话的创建、流式响应、历史记录管理等功能。"""

# ── 复制自原模块的顶层 import ──

import asyncio

import json

import logging

import uuid

from fastapi import APIRouter, HTTPException, Request

from fastapi.responses import StreamingResponse

from app.config import settings

from app.context.token_counter import estimate_tokens

from app.context.budget import usable_context_tokens

from app.middleware.summarization import HierarchicalSummarizationMiddleware

from app.models.schemas import ChatRequest, Source, StepEvent, MultiAgentChatResponse

from app.session import repository as session_repo

from app.session import task_bridge

from app.session.agent_executor import classify_error, PartBridgeQueue

from app.session.deps import discover_project_root

from app.agent.base import AgentMessage

from app.agent.bus import AgentBus

from app.agent.stream_events import AgentEventCollector

logger = logging.getLogger(__name__)

# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──


# --- 用户身份：优先从 X-User-Id 头获取，默认 anonymous ---

_DEFAULT_USER_ID = "anonymous"

def _get_user_id(request: Request) -> str:
    """从请求头中提取用户身份。

    B12: 记录 (user_id, 来源 IP) 审计日志。
    """
    uid = request.headers.get("X-User-Id", "")
    user_id = uid.strip() if uid.strip() else _DEFAULT_USER_ID
    client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )
    logger.debug("user_id=%s ip=%s path=%s", user_id, client_ip, request.url.path)
    return user_id



# [token 优化 v3] Sliding window: keep up to 32K tokens of history before passing to Agent.
# graph.py 通过 config.max_context_tokens(v9 后为 24K，usable ≈ 15.8K) 做上下文管理，此阈值仅控制历史注入量。
# [token 优化 v9] 32K → 16K：历史注入量减半，长会话首条消息不再重发整段 32K 历史。
# [B9] 历史注入预算与上下文配置联动：min(16K, usable_context_tokens())。
#   usable 缩小时（reserve 变大）预算自动缩小，防止注入量超出可用上下文；
#   usable 放大时仍封顶 16K，不扩大既有行为。消除与 budget.py 的双常量漂移。

MAX_HISTORY_TOKENS = max(1, min(16_000, usable_context_tokens()))


# 聊天消息长度上限（与前端 ChatInput.vue 的 MAX_LENGTH 对齐，前端+后端双层约束）

MAX_MESSAGE_LENGTH = 50_000

_summarizer: HierarchicalSummarizationMiddleware | None = None

_summarizer_model: str | None = None

def _get_summarizer() -> HierarchicalSummarizationMiddleware | None:
    """获取或初始化摘要中间件单例。"""
    global _summarizer, _summarizer_model
    current_model = settings.summarization_model

    if current_model != _summarizer_model:
        _summarizer = None
        _summarizer_model = current_model

    if _summarizer is not None:
        return _summarizer
    if not current_model:
        return None
    _summarizer = HierarchicalSummarizationMiddleware(
        model=current_model,
        trigger=("tokens", MAX_HISTORY_TOKENS),
        keep=("messages", settings.summarization_keep_messages),
        api_key=settings.summarization_api_key or settings.llm_api_key,
        api_base=settings.summarization_api_base or settings.llm_api_base,
    )
    return _summarizer

def reset_summarizer():
    """重置摘要中间件状态。"""
    global _summarizer, _summarizer_model
    _summarizer = None
    _summarizer_model = None

def _generate_title(messages: list[dict]) -> str:
    """根据用户第一条消息生成对话标题。

    B8: 清洗控制字符 + html.escape 防注入，字节安全截断。
    """
    import html
    import re
    for msg in messages:
        if msg.get("role") == "user":
            text = msg.get("content", "")
            # 去除控制字符（保留换行/制表）
            text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
            text = text.strip().replace("\n", " ")
            text = html.escape(text)
            return text[:20] + ("..." if len(text) > 20 else "")
    return "新对话"

def _truncate_history(history: list[dict], max_tokens: int = MAX_HISTORY_TOKENS) -> list[dict]:
    """截断对话历史以控制token数量。确保至少保留最近一条消息。"""
    if not history:
        return []
    total = 0
    truncated = []
    # Always keep at least the last message
    for msg in reversed(history):
        tokens = estimate_tokens(msg.get("content", ""))
        if total + tokens > max_tokens and truncated:
            truncated.append({"role": "system", "content": "[earlier history truncated]"})
            break
        total += tokens
        truncated.append(msg)
    truncated.reverse()
    return truncated



# 发给模型时只保留必要字段，去掉 id / steps / sources 等内部字段

_ALLOWED_HISTORY_KEYS = {"role", "content", "name", "tool_call_id", "tool_calls"}

def _sanitize_history(history: list[dict]) -> list[dict]:
    """清洗历史消息：仅保留模型需要的最小字段集合。

    DB 中的消息带有 id（前端编辑用）、steps（工具调用过程，含 tool_args/tool_result）、
    sources 等字段。这些字段原样塞进 LLM 请求会白白增大请求体积（且部分网关会拒绝），
    这里统一剥离。
    """
    cleaned = []
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in ("user", "assistant", "system", "tool"):
            continue
        out = {k: v for k, v in msg.items() if k in _ALLOWED_HISTORY_KEYS}
        # 至少要有内容或工具调用，否则跳过
        if out.get("content") is None and not out.get("tool_calls"):
            continue
        cleaned.append(out)
    return cleaned



# ═══════════════════════════════════════════════════════════════
#  Session 管理接线（设计文档 P3）：/stream 走 SessionService，
#  会话 CRUD 统一读 session.db
# ═══════════════════════════════════════════════════════════════


def _get_session_service(request: Request):
    return request.app.state.session_service

def _msg_type_to_role(msg_type: str) -> str:
    return {
        "user": "user", "assistant": "assistant", "tool": "tool",
        "compaction": "system", "epoch": "system", "system": "system",
    }.get(msg_type, "system")

def _validate_chat_message(body: ChatRequest) -> None:
    """聊天消息兜底校验：空内容/超长直接 422（与 schema 约束双层防护）。

    schema 的 Field(min_length/max_length) 已拦截超长输入，此处兜底处理
    纯空白消息（len>0 但 strip 后为空）并给出友好提示。
    """
    msg = body.message
    if not msg or not msg.strip():
        raise HTTPException(status_code=422, detail="消息内容不能为空")
    if len(msg) > MAX_MESSAGE_LENGTH:
        raise HTTPException(status_code=422, detail=f"消息长度超出上限（{MAX_MESSAGE_LENGTH} 字符）")



__all__ = ["MAX_HISTORY_TOKENS", "MAX_MESSAGE_LENGTH", "_ALLOWED_HISTORY_KEYS", "_DEFAULT_USER_ID", "_generate_title", "_get_session_service", "_get_summarizer", "_get_user_id", "_msg_type_to_role", "_sanitize_history", "_summarizer", "_summarizer_model", "_truncate_history", "_validate_chat_message", "reset_summarizer"]
