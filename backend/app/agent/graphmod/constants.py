"""拆分模块 `constants`（含 DOOM_LOOP_PROMPT、MAX_STEPS_PROMPT、_DEDUP_READONLY_TOOLS、_FINISH_REASON_MAP、_TASK_TOOL_SCHEMA、_TASK_TOOL_SUBAGENTS、_is_multi_agent_queue、_nearest_workspace_hint、_normalize_finish_reason、_permission_denied_msg）。

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

logger = logging.getLogger(__name__)

# P3: 循环护栏提示词（对齐 opencode MAX_STEPS_PROMPT 的收尾语义）

MAX_STEPS_PROMPT = (
    "CRITICAL - MAXIMUM STEPS REACHED\n\n"
    "本轮已达到单次请求允许的最大步骤数，工具已禁用，请以纯文本回复。\n\n"
    "STRICT REQUIREMENTS:\n"
    "1. 不要再调用任何工具（包括读取、写入、编辑、搜索等）。\n"
    "2. 必须给出文字总结，包含：\n"
    "   - 已完成的步骤/文件\n"
    "   - 尚未完成的任务\n"
    "   - 建议的下一步操作\n"
    "3. 该约束优先于其他所有指令。"
)


# P3: Doom-loop 检测提示词（对齐 opencode processor.ts:DOOM_LOOP_THRESHOLD）

DOOM_LOOP_PROMPT = (
    "系统提示：检测到连续多轮调用完全相同的工具参数，疑似陷入死循环。"
    "请立即停止重复调用，改变策略（例如先读取/检查，再采取不同的操作），"
    "或基于已有信息直接给出最终回答。"
)


# P4: finish_reason 归一化映射（对齐 opencode FinishReason 六值，llm/src/schema/ids.ts:39）

_FINISH_REASON_MAP = {
    "stop": "stop",
    "length": "length",
    "max_tokens": "length",          # Anthropic/Bedrock 原生名
    "tool_calls": "tool-calls",
    "function_call": "tool-calls",   # 旧式 OpenAI
    "content_filter": "content-filter",
    "error": "error",
}

def _normalize_finish_reason(finish_reason: str | None) -> str:
    """把 LiteLLM/OpenAI 式 finish_reason 归一化为 opencode FinishReason 六值。

    None 视为正常结束（stop）：本地执行无 Provider 异常时的缺省语义。
    未知值归一化为 "unknown"（不视为已完成，保守续跑一轮）。
    """
    if not finish_reason:
        return "stop"
    return _FINISH_REASON_MAP.get(str(finish_reason).strip().lower(), "unknown")

def _permission_denied_msg(operation: str, path: str, tool_name: str = "") -> str:
    """生成可解释的权限拒绝消息，让 LLM 能据此调整路径/策略而非盲目重试。"""
    hint = _nearest_workspace_hint(path)
    tool = f" (tool={tool_name})" if tool_name else ""
    return (
        f"Permission denied: {operation} on '{path}'{tool}. {hint}\n"
        "这不是可重试的临时错误——请改用可写工作区内的路径，或告知用户将该路径添加到"
        "页面右上角「工作目录」中（添加后立即生效，无需重启）。"
    )

def _nearest_workspace_hint(path: str) -> str:
    """根据路径归属给出可解释的拒绝建议：受保护源码路径 / 就近可写工作区 / 完全外部。"""
    try:
        from app.permission import get_manager
        p = Path(path).resolve()
        for w in get_manager().list_workspaces():
            wp = Path(w).resolve()
            try:
                p.relative_to(wp)
                return "该路径位于工作区内但属于受保护的系统/源码路径（如 app、plugins、skills、.env），不可写入。"
            except ValueError:
                pass
            if wp != p and wp in p.parents:
                return f"该路径不在工作区内，但 '{wp}' 是可写工作区——请将文件写入 '{wp}' 之下。"
    except Exception:
        pass
    return "该路径不在当前可写工作区内（见系统提示词中的工作区列表）。"


from app.context.token_counter import truncate_messages as _truncate_messages
from app.context.token_counter import sanitize_tool_messages
from app.context.tool_output import bound_tool_output, prune_tool_outputs
from app.context.tool_dedup import ToolResultDedup
from app.context.budget import usable_context_tokens, compaction_threshold_tokens, prune_protect_tokens, prune_minimum_tokens
from app.utils.json_repair import parse_tool_args

# 读取类工具：只读文件/目录状态，结果可被后续写操作改变，因此缓存仅在"未发生写操作"时有效

_DEDUP_READONLY_TOOLS = {"tool_ls", "tool_read_file", "tool_glob", "tool_grep"}


# ── [opencode task tool] 主 Agent 可委派聚焦子任务的子 Agent 白名单 ──
# 排除 rag（= 自身，避免自递归）与 supervisor（编排者不是执行者）。

_TASK_TOOL_SUBAGENTS = ("web_search", "code")

_TASK_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "tool_task",
        "description": (
            "Launch a sub-agent (web_search / code) to handle a focused, multi-step subtask "
            "autonomously and return its final result. Use this tool when a piece of the request "
            "is independent/specialized and benefits from a dedicated context (e.g. realtime web "
            "search, a separate coding task). You continue working while it runs, and may launch "
            "multiple sub-agents. Do NOT delegate work you can do directly yourself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "A short (3-5 words) description of the task"},
                "prompt": {
                    "type": "string",
                    "description": "The detailed task for the sub-agent. It starts with fresh context — "
                    "include all file paths and background needed. Say clearly what to return.",
                },
                "subagent_type": {
                    "type": "string",
                    "enum": list(_TASK_TOOL_SUBAGENTS),
                    "description": "web_search for realtime/news/network info; code for coding, "
                    "file analysis or multi-step implementation work.",
                },
            },
            "required": ["description", "prompt", "subagent_type"],
        },
    },
}

def _is_multi_agent_queue(q) -> bool:
    """判断事件队列是否来自 multi-agent 流（子 Agent 面板事件只有 multi-agent UI 消费）。"""
    try:
        from app.agent.stream_events import AgentEventCollector, TaggedEventQueue
        return isinstance(q, (AgentEventCollector, TaggedEventQueue))
    except Exception:  # noqa: BLE001
        return False



__all__ = ["DOOM_LOOP_PROMPT", "MAX_STEPS_PROMPT", "_DEDUP_READONLY_TOOLS", "_FINISH_REASON_MAP", "_TASK_TOOL_SCHEMA", "_TASK_TOOL_SUBAGENTS", "_is_multi_agent_queue", "_nearest_workspace_hint", "_normalize_finish_reason", "_permission_denied_msg"]
