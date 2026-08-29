"""拆分模块 `state`（含 AgentState、_ZERO_USAGE、_attachment_parts、_extract_cache_usage、_find_attachment）。

原文件 docstring: (无)"""

# ── 复制自原模块的顶层 import ──

import asyncio

import inspect

import logging

import os

import shlex

import subprocess

import threading

import time as tmod

import uuid

from collections.abc import Sequence

from pathlib import Path

from typing import Annotated, Callable, TypedDict

from app.context.token_counter import truncate_messages as _truncate_messages

from app.context.token_counter import sanitize_tool_messages

from app.context.tool_output import bound_tool_output, prune_tool_outputs

from app.context.tool_dedup import ToolResultDedup

from app.context.budget import usable_context_tokens, compaction_threshold_tokens, prune_protect_tokens, prune_minimum_tokens

from app.utils.json_repair import parse_tool_args

import litellm

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

from langgraph.graph import StateGraph, END

from app.agent.base import AgentMessage

from app.rag.retriever import Retriever

from app.rag.reranker import Reranker

from app.skills.loader import SkillLoader

from app.plugins.loader import PluginLoader

from app.config import settings

from app.agent.tools import (
    ToolDef,
    LONG_CONTENT_FILE_RULE,
    create_filesystem_tools,
    create_skill_tools,
    create_plugin_tools,
    build_system_prompt_no_kb,
)

from app.skills.custom_tools import CustomToolStore  # [token 优化 v6]

from app.monitor import record_model_call

from app.trace_log import trace, trace_messages  # [token trace v7]

from app.prompt_log import log_prompt  # [prompt log v1]

from app.permission import NeedsPermission, get_manager as get_perm_mgr

logger = logging.getLogger(__name__)

# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──


import litellm
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END
from app.agent.base import AgentMessage
from app.rag.retriever import Retriever
from app.rag.reranker import Reranker
from app.skills.loader import SkillLoader
from app.plugins.loader import PluginLoader
from app.config import settings
from app.agent.tools import (
    ToolDef,
    LONG_CONTENT_FILE_RULE,
    create_filesystem_tools,
    create_skill_tools,
    create_plugin_tools,
    build_system_prompt_no_kb,
)
from app.skills.custom_tools import CustomToolStore  # [token 优化 v6]
from app.monitor import record_model_call
from app.trace_log import trace, trace_messages  # [token trace v7]
from app.prompt_log import log_prompt  # [prompt log v1]
from app.permission import NeedsPermission, get_manager as get_perm_mgr



class AgentState(TypedDict):
    """代理状态类型定义，包含对话过程中的所有状态信息。"""
    messages: Annotated[Sequence[BaseMessage], "messages"]
    question: str
    context: list[dict]
    answer: str
    sources: list[dict]
    model: str | None
    history: list[dict]
    use_vector_db: bool
    files: list[dict]
    steps: list[dict]
    tokens: dict[str, int]
    finish: str
    _event_queue: asyncio.Queue | None
    _on_activity: Callable[[str], None] | None
    _task: object | None
    _cwd: str
    _task_depth: int
    conversation_id: str = ""

_ZERO_USAGE = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}

def _extract_cache_usage(usage, pt: int = 0):
    """从 LLM usage 提取前缀缓存命中/未命中 token。

    DeepSeek 原生字段为 usage.prompt_tokens_details.cached_tokens；
    litellm 透传为 usage.prompt_cache_hit_tokens / prompt_cache_miss_tokens。
    两者取一，返回 (hit, miss)；miss 缺失时用 pt - hit 兜底，保证 hit + miss == pt。
    """
    if usage is None:
        return 0, 0
    hit = int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0)
    miss = int(getattr(usage, "prompt_cache_miss_tokens", 0) or 0)
    if not hit and not miss:
        det = getattr(usage, "prompt_tokens_details", None)
        if det is not None:
            hit = int(getattr(det, "cached_tokens", 0) or 0)
    if miss == 0 and pt > hit:
        miss = pt - hit
    return hit, miss

def _find_attachment(files: list[dict], filename: str) -> dict:
    for f in files:
        if f.get("filename") == filename:
            return f
    raise KeyError(filename)

def _attachment_parts(files: list[dict], budget: int = 6000):
    """把附件拆分为 (图片文件名列表, 文本上下文)。

    图片交给多模态 image_url；文档附件经 LangChain loaders
    （attachment_loader.attachment_context_text）解析为文本上下文。
    """
    from .. import attachment_loader

    image_names: list[str] = []
    others: list[dict] = []
    for f in files:
        mime = (f.get("mime_type") or "").lower()
        ext = Path((f.get("filename") or "")).suffix.lower()
        if mime.startswith("image/") or ext in {".jpg", ".jpeg", ".png", ".gif",
                                                ".webp", ".bmp", ".svg", ".ico"}:
            image_names.append(f.get("filename") or "file")
        else:
            others.append(f)
    text_ctx = attachment_loader.attachment_context_text(others, budget=budget) if others else ""
    return image_names, text_ctx



__all__ = ["AgentState", "_ZERO_USAGE", "_attachment_parts", "_extract_cache_usage", "_find_attachment"]
