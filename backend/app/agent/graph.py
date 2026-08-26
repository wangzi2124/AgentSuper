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


class RAGAgent:
    """RAG（检索增强生成）代理，负责协调检索、重排序和生成回答的流程。"""

    def __init__(
        self,
        retriever: Retriever,
        skill_loader: SkillLoader | None = None,
        plugin_loader: PluginLoader | None = None,
        reranker: Reranker | None = None,
        custom_tools: CustomToolStore | None = None,  # [token 优化 v6] 前端添加的自定义工具/固定工具
        memory=None,  # [opencode memory] 共享记忆管理器（runtime.py 注入）
    ):
        self.retriever = retriever
        self.reranker = reranker
        self.skill_loader = skill_loader
        self.plugin_loader = plugin_loader
        self.custom_tools = custom_tools
        self.memory = memory
        self.model = settings.llm_model
        self.api_key = settings.llm_api_key
        self.api_base = settings.llm_api_base

        self.tools: list[ToolDef] = []
        self.tools.extend(create_filesystem_tools())
        if skill_loader:
            self.tools.extend(create_skill_tools(skill_loader))
        if plugin_loader:
            self.tools.extend(create_plugin_tools(plugin_loader))
        # [opencode task tool] 主 Agent 自主委派子 Agent（web_search / code）。
        # fn 仅为 schema 占位：实际执行在 _execute_tool 中特判（需注入任务深度与事件队列）。
        self.tools.append(ToolDef(
            name="tool_task",
            description=_TASK_TOOL_SCHEMA["function"]["description"],
            parameters=_TASK_TOOL_SCHEMA["function"]["parameters"],
            fn=self._task_tool_placeholder,
        ))
        # [opencode memory] 主 Agent 记忆读写工具：模型可主动记忆/回忆关键信息。
        # 仅在注入了共享记忆管理器时注册（fn 为占位，实际执行在 _execute_tool 特判）。
        if memory is not None:
            self.tools.append(ToolDef(
                name="tool_memory_set",
                description=(
                    "记住一条需要跨轮次可靠保留的稳定事实（会话内有效）。"
                    "仅存值得长期保留的信息：用户明确表达的偏好、项目关键决策/结论、"
                    "重要的标识符/数值。克制使用——临时信息（可当场说完的、很快过期不用的）"
                    "不要存；对话历史本身已能覆盖的内容不要重复记忆。"
                    "可指定标签便于后续按主题检索。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "记忆键（如 'user_language_preference'）"},
                        "value": {"type": "string", "description": "要记住的内容（尽量简洁）"},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "可选标签，用于按主题检索"},
                    },
                    "required": ["key", "value"],
                },
                fn=self._memory_tool_placeholder,
            ))
            self.tools.append(ToolDef(
                name="tool_memory_get",
                description=(
                    "按 key 读取一条已记住的信息。用于回忆之前 tool_memory_set 保存的内容。"
                    "未找到或已过期时返回空。"
                ),
                parameters={
                    "type": "object",
                    "properties": {"key": {"type": "string", "description": "要读取的记忆键"}},
                    "required": ["key"],
                },
                fn=self._memory_tool_placeholder,
            ))
            self.tools.append(ToolDef(
                name="tool_memory_search",
                description=(
                    "按标签检索所有相关的已记住信息。用于在本会话内按主题查找记忆"
                    "（如标签 'project'、'user_preference'）。返回匹配的键值对。"
                ),
                parameters={
                    "type": "object",
                    "properties": {"tag": {"type": "string", "description": "要检索的标签"}},
                    "required": ["tag"],
                },
                fn=self._memory_tool_placeholder,
            ))

        # 子 Agent 消息总线（runtime.py 注入）：供 tool_task 委派使用
        self.task_bus: object | None = None

        self.system_prompt = build_system_prompt_no_kb(
            skill_loader or SkillLoader(""),
            plugin_loader or PluginLoader(""),
            include_filesystem=True,
            has_memory=memory is not None,
        )

        self._usage_accum: dict[str, int] = dict(_ZERO_USAGE)
        # 工具热更新锁：refresh_tools 重建 graph 期间与并发请求互斥
        self._refresh_lock = asyncio.Lock()

        self.graph = self._build_graph()

    def rebuild_system_prompt(self):
        """仅重建系统提示词（不重建图），用于工作区/技能/插件变化后的热更新。

        工作区列表由 build_system_prompt_no_kb 动态读取权限管理器，无需重启。
        """
        self.system_prompt = build_system_prompt_no_kb(
            self.skill_loader or SkillLoader(""),
            self.plugin_loader or PluginLoader(""),
            include_filesystem=True,
            has_memory=getattr(self, "memory", None) is not None,
        )

    def _activity_text(self, event: dict) -> str:
        """把事件转成简短的处理进度描述，用于子 Agent 心跳/超时回传。"""
        et = event.get("type")
        if et == "tool_start":
            return f"调用工具: {event.get('tool_name', '')}"
        if et == "tool_end":
            return f"完成工具: {event.get('tool_name', '')}"
        if et in ("tool_output", "tool_heartbeat"):
            return f"{event.get('tool_name', '')} 运行中 ({event.get('elapsed_seconds', '?')}s)"
        name = event.get("name")
        if name:
            return str(name)
        return et or ""

    def _push_event(self, state: AgentState, event: dict):
        """将事件推送到状态和事件队列中，用于实时通知前端。"""
        state["steps"].append(event)
        eq = state.get("_event_queue")
        if eq:
            try:
                eq.put_nowait(event)
            except Exception:
                logger.warning("push_event: failed to enqueue event %s", event.get("type"))
        cb = state.get("_on_activity")
        if cb:
            text = self._activity_text(event)
            if text:
                try:
                    cb(text)
                except Exception:
                    pass

    async def _retrieve(self, state: AgentState) -> dict:
        """从知识库中检索与问题相关的文档片段。"""
        start = tmod.time()
        self._push_event(state, {"type": "step_start", "step_id": "retrieve", "name": "检索中", "status": "running"})

        if self.retriever.is_empty or not state.get("use_vector_db", False):
            reason = "知识库为空" if self.retriever.is_empty else "已禁用向量检索"
            dur = (tmod.time() - start) * 1000
            self._push_event(state, {"type": "step_end", "step_id": "retrieve", "name": "检索中", "status": "completed", "detail": reason, "duration_ms": round(dur, 1)})
            return {"context": [], "sources": []}

        import functools
        try:
            results = await asyncio.to_thread(
                functools.partial(self.retriever.invoke, state["question"], k=3)  # [token 优化 v2] 5->3
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("retrieve failed, returning empty context: %s", e)
            results = []
        context = []
        sources = []
        for doc, score in results:
            meta = doc["metadata"]
            context.append({"content": doc["text"], "metadata": meta})
            sources.append({
                "document_id": meta.get("document_id", ""),
                "content": doc["text"][:300],
                "score": score,
            })
        dur = (tmod.time() - start) * 1000
        self._push_event(state, {"type": "step_end", "step_id": "retrieve", "name": "检索中", "status": "completed", "detail": f"找到 {len(results)} 个相关片段", "duration_ms": round(dur, 1)})
        return {"context": context, "sources": sources}

    async def _rerank(self, state: AgentState) -> dict:
        """对检索结果进行相关性重排序，筛选最相关的片段。"""
        if not self.reranker or not state.get("context"):
            if state.get("context"):
                self._push_event(state, {"type": "step_end", "step_id": "rerank", "name": "相关性重排序", "status": "completed", "detail": "重排序已禁用"})
            return {}
        start = tmod.time()
        self._push_event(state, {"type": "step_start", "step_id": "rerank", "name": "相关性重排序", "status": "running"})
        import functools
        reranked = await asyncio.to_thread(
            functools.partial(self.reranker.rerank, query=state["question"], documents=state["context"], top_k=3)
        )
        context = [{"content": doc["content"], "metadata": doc["metadata"]} for doc, _ in reranked]
        dur = (tmod.time() - start) * 1000
        self._push_event(state, {"type": "step_end", "step_id": "rerank", "name": "相关性重排序", "status": "completed", "detail": f"筛选出 {len(reranked)} 个最相关片段", "duration_ms": round(dur, 1)})
        return {"context": context}

    def _system_prompt_with_kb(self) -> str:
        """构建包含知识库上下文的系统提示词。"""
        return (
            "You are a knowledgeable AI assistant with access to a knowledge base."
            "\n\nUse the retrieved context below to answer the user's question."
            "\n- Cite sources using [Source 1], [Source 2], etc."
            "\n- If a source has 'chapter_title' in its metadata, use that exact title when referring to the chapter."
            "\n- If a source has 'chapter_summary', it is a chapter overview — use it to describe the chapter's content."
            "\n- If you don't have enough information, say so."
            "\n\nYou have access to built-in filesystem tools (tool_ls, tool_read_file, tool_write_file, tool_append_file, tool_edit_file, tool_glob, tool_grep, tool_execute, tool_apply_patch) for reading/writing files, running shell commands and applying patches."
            "\nYou also have access to skill tools (load_skill_*) and plugin tools."
            "\nIf the user asks to create/edit/manipulate documents (Word, PDF, PPT, Excel), generate visual designs, build web pages, or use other specialized capabilities, call the relevant skill or plugin tool to get instructions first."
            "\n\nCharacter Analysis (for novels, scripts, or documents with dialogues):"
            "\n- plugin_character-analysis_tool_list_characters(): List all characters and their dialogue counts."
            "\n- plugin_character-analysis_tool_get_character_dialogues(character_name, limit): Get all dialogues spoken by a character."
            "\n- plugin_character-analysis_tool_analyze_character_interactions(character_name): Find characters who appear in same chapters."
            "\n\nYou also have a task tool: tool_task(description, prompt, subagent_type). "
            "Use it to delegate independent or specialized subtasks (realtime web search via 'web_search', "
            "or a separate coding/file task via 'code') to a sub-agent and get its final result back — "
            "it runs with fresh context, so include all needed details. Do NOT delegate work you can do directly."
            "\n\n"
            + (
                "IMPORTANT - Shell dialect: commands run via cmd.exe on Windows, NOT bash. Use backslash paths "
                "for executables (.venv\\Scripts\\python.exe — forward slashes fail), %ERRORLEVEL% instead of $?, "
                "and & / && instead of ; as command separator."
                "\n\n"
                if os.name == "nt"
                else ""
            )
            + LONG_CONTENT_FILE_RULE
        )

    # [token 优化 v5] 按需挂载工具 schema：核心文件工具常驻，技能/插件按意图关键词 + 已使用保留
    _CORE_TOOL_PREFIXES = ("tool_",)
    _WEATHER_TOOL_PREFIXES = ("plugin_weather", "plugin_weather-alert")
    _WEATHER_RESULT_LIMIT = 1500  # 字符
    # 意图关键词 → 需要挂载的工具名前缀（任一词命中即挂载该类工具）
    _INTENT_RULES: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
        (("天气", "台风", "气象", "温度", "降雨", "下雪", "weather", "typhoon", "forecast"),
         ("plugin_weather", "plugin_weather-alert")),
        (("文档", "word", "docx", "pdf", "excel", "xlsx", "ppt", "pptx", "表格", "幻灯片", "报告"),
         ("plugin_docx-generator", "plugin_pdf-generator", "plugin_excel-generator", "plugin_pptx-generator",
          "load_skill_docx", "load_skill_pdf", "load_skill_xlsx", "load_skill_pptx", "load_skill_doc_coauthoring")),
        (("网页", "前端", "react", "vue", "html", "css", "网站", "页面", "artifact", "frontend", "web"),
         ("load_skill_frontend_design", "load_skill_web_artifacts_builder", "load_skill_webapp_testing",
          "load_skill_theme_factory", "load_skill_canvas_design")),
        (("搜索", "查一下", "新闻", "资讯", "上网", "search", "news", "internet"),
         ("plugin_internet-search_",)),
        (("图片", "海报", "设计", "艺术", "绘图", "生成图", "image", "poster", "art", "draw"),
         ("load_skill_canvas_design", "load_skill_algorithmic_art", "load_skill_slack_gif_creator")),
        (("语音", "声音", "配音", "克隆", "合成", "voice", "audio", "speech"),
         ("plugin_voice-clone_",)),
        (("角色", "人物", "对话", "台词", "character", "dialogue"),
         ("plugin_character-analysis_",)),
        (("知识库", "kb", "导出"),
         ("plugin_kb-export_",)),
        (("代码", "编程", "bug", "调试", "重构", "code", "debug", "test", "tdd", "review", "实现"),
         ("load_skill_tdd", "load_skill_code_review", "load_skill_diagnosing_bugs", "load_skill_implement",
          "load_skill_to_tickets", "load_skill_grilling", "load_skill_grill_me", "load_skill_codebase_design")),
        (("技能", "skill"),
         ("load_skill_",)),
        (("插件", "plugin"),
         ("plugin_",)),
        (("教学", "学习", "teach"),
         ("load_skill_teach",)),
        (("研究", "research"),
         ("load_skill_research",)),
        (("模型", "api", "claude", "大模型"),
         ("load_skill_claude_api",)),
        (("架构", "模块", "设计模式", "architecture"),
         ("load_skill_codebase_design", "load_skill_domain_modeling", "load_skill_improve_codebase_architecture")),
    ]

    def _tool_matches_intent(self, t: ToolDef, question_lower: str) -> bool:
        """意图关键词命中：问题包含关键词且工具名前缀匹配 → 挂载该工具 schema。"""
        for keywords, prefixes in self._INTENT_RULES:
            if not any(k in question_lower for k in keywords):
                continue
            if t.name.startswith(prefixes):
                return True
        return False

    def _build_tool_defs(self, question: str = "", used_names: set | None = None) -> list[dict] | None:
        """[token 优化 v5] 按需挂载 OpenAI 工具定义。

        system prompt 只列常驻工具名 + 一行 skill 提示（[token 优化 v10]），技能清单与
        描述依赖此处按意图把 load_skill_* schema 挂载给 LLM；此处只发本轮可能用到的
        schema：核心文件工具常驻 + 意图关键词命中 + 已使用工具保留。
        schema 固定开销从 8-12K 降到 2-4K。若模型调用了未挂载工具，
        _execute_tool 仍可执行（self.tools 全量），下一轮该工具自动保留。
        """
        if not self.tools:
            return None
        used = used_names or set()
        q = (question or "").lower()
        # [token 优化 v6] 固定（pin）工具集合只读一次，避免循环内反复读 pinned_tools.json
        pinned = self._pinned_tool_names()
        selected: list[ToolDef] = []
        for t in self.tools:
            # [token 优化 v6] 前端固定（pin）的工具始终挂载，不受意图筛选影响
            if t.name in pinned:
                selected.append(t)
                continue
            if t.name in used:
                selected.append(t)
                continue
            if t.name.startswith(self._CORE_TOOL_PREFIXES):
                selected.append(t)
                continue
            if self._tool_matches_intent(t, q):
                selected.append(t)
                continue
        return [t.to_openai_tool() for t in selected]

    def _pinned_tool_names(self) -> set[str]:
        """[token 优化 v6] 返回前端固定（pin）的工具名集合（始终挂载 schema）。"""
        try:
            if self.custom_tools:
                return set(self.custom_tools.pinned_tools())
        except Exception:
            pass
        return set()

    def _bound_plugin_result(self, name: str, result) -> str:
        """[token 优化 v5] 大块结构化插件结果（天气/台风）截断，避免整块数据躺进历史每轮重发。

        兼容新版工具信封 {title, metadata, output}：先解包 output 再截断。
        """
        from app.tools.file_tools import unwrap
        text = unwrap(result)
        if name.startswith(self._WEATHER_TOOL_PREFIXES) and len(text) > self._WEATHER_RESULT_LIMIT:
            return text[:self._WEATHER_RESULT_LIMIT] + "\n…[已截断：天气/台风数据过长，仅保留前 1500 字符]"
        return text

    def _task_tool_placeholder(self, description: str = "", prompt: str = "", subagent_type: str = "web_search") -> str:
        """占位实现：_execute_tool 对 tool_task 特判，这里不会被真正调用。"""
        return ""

    def _memory_tool_placeholder(self, **kwargs) -> str:
        """占位实现：_execute_tool 对 tool_memory_* 特判，这里不会被真正调用。"""
        return ""

    async def _tool_task(self, args: dict, depth: int = 0, event_queue=None, directory: str = "", conversation_id: str = "") -> str:
        """[opencode task tool] 把聚焦子任务委派给子 Agent 并取回最终结果。

        - subagent_type 白名单排除 rag（= 自身）与 supervisor（编排者非执行者），防自递归
        - 嵌套深度受 settings.subagent_depth 限制（默认 1 = 主 Agent 只能再委派一层，对齐 opencode）
        - 子 Agent 以全新上下文执行（对齐 opencode task 工具 "fresh context" 语义），
          事件队列仅当来自 multi-agent 流时才透传（单 Agent 流不推子 Agent 面板事件）；
          会话工作目录（directory）透传给子 Agent，文件工具落在会话目录而非 git worktree
        - conversation_id 透传外层会话 id，保证子 Agent 与主 Agent 落在同一记忆 namespace
          （否则子 Agent 记忆读写落在全局 namespace，与主 Agent 会话隔离记忆互不可见）
        """
        prompt = str(args.get("prompt") or "").strip()
        subagent_type = str(args.get("subagent_type") or "").strip()
        if not prompt:
            return "Error: 'prompt' is required for tool_task."
        if subagent_type not in _TASK_TOOL_SUBAGENTS:
            return f"Error: unknown subagent_type '{subagent_type}'. Valid: {sorted(_TASK_TOOL_SUBAGENTS)}."
        bus = getattr(self, "task_bus", None)
        if bus is None:
            return "Error: sub-agent bus is unavailable (tool_task disabled)."
        max_depth = max(1, settings.subagent_depth)
        if depth >= max_depth:
            return (
                f"Error: sub-agent depth limit reached ({max_depth}). "
                f"Increase SUBAGENT_DEPTH (default 1) to allow nested sub-agents."
            )

        # 工具密集型子 Agent 使用更长等待，避免长任务被误判超时（对齐 supervisor._timeout_for）
        timeout = settings.sub_agent_timeout_extended if subagent_type == "code" else settings.sub_agent_timeout
        sub_thread_id = f"task:{uuid.uuid4().hex[:8]}"
        reply = await bus.send_and_wait(
            AgentMessage(
                source="user",
                target=subagent_type,
                type="request",
                action="chat",
                payload={
                    "question": prompt,
                    "model": self.model,
                    "history": [],
                    "use_vector_db": False,
                    "files": [],
                    "conversation_id": conversation_id,
                    "directory": directory,
                    "_task_depth": depth + 1,
                    "_event_queue": event_queue,
                },
                thread_id=sub_thread_id,
            ),
            timeout=timeout,
        )
        if reply.type == "response":
            return reply.payload.get("answer", "") or "(no answer)"
        err = reply.payload.get("error", "sub-agent failed")
        return f"Error: sub-agent '{subagent_type}' failed: {err}"

    async def _tool_memory(self, name: str, args: dict, state: AgentState | None) -> str:
        """[opencode memory] 主 Agent 记忆读写：set/get/search。

        基于共享 MemoryManager（runtime 注入），会话内按 conversation_id 隔离。
        与子 Agent 共用同一实例，因此主 Agent 记住的信息子 Agent 也可检索到
        （对齐 opencode 的共享上下文语义）。
        """
        mm = getattr(self, "memory", None)
        if mm is None:
            return "Error: memory is unavailable."
        namespace = str((state or {}).get("conversation_id") or "")

        try:
            if name == "tool_memory_set":
                key = str(args.get("key") or "").strip()
                value = args.get("value")
                if not key:
                    return "Error: 'key' is required."
                tags = args.get("tags") or []
                if not isinstance(tags, list):
                    tags = [str(tags)] if tags else []
                tags = [str(t) for t in tags if str(t).strip()]
                await mm.set(key, value, ttl=settings.memory_ttl_seconds, tags=tags, namespace=namespace)
                return f"记住成功: key='{key}' (会话内有效, 标签: {tags or '无'})"
            if name == "tool_memory_get":
                key = str(args.get("key") or "").strip()
                if not key:
                    return "Error: 'key' is required."
                val = await mm.get(key, default=None, namespace=namespace)
                if val is None:
                    return f"未找到 key='{key}'（可能未记住或已过期）"
                return f"{key}: {val}"
            if name == "tool_memory_search":
                tag = str(args.get("tag") or "").strip()
                if not tag:
                    return "Error: 'tag' is required."
                found = await mm.get_by_tag(tag, namespace=namespace)
                if not found:
                    return f"未找到标签 '{tag}' 相关的记忆"
                lines = [f"{k}: {v}" for k, v in found.items()]
                return f"标签 '{tag}' 相关记忆:\n" + "\n".join(lines)
            return f"Error: unknown memory tool '{name}'"
        except Exception as e:
            logger.warning("Memory tool %s failed: %s", name, e)
            return f"Error executing {name}: {e}"

    async def _execute_tool(self, name: str, args: dict, state: dict | None = None) -> str:
        """执行指定的工具函数，处理权限检查和错误。"""
        for t in self.tools:
            if t.name == name:
                try:
                    # [opencode task tool] 主 Agent 自主委派子 Agent 的入口（注入任务深度 + 事件队列）
                    if name == "tool_task":
                        depth = int((state or {}).get("_task_depth", 0) or 0)
                        eq = (state or {}).get("_event_queue")
                        if not _is_multi_agent_queue(eq):
                            eq = None  # 单 Agent 流不推子 Agent 面板事件
                        else:
                            # 剥掉委派方自己的 TaggedEventQueue，让子 Agent 用自己的 id 重新打标签
                            from app.agent.stream_events import unwrap_tagged
                            eq = unwrap_tagged(eq)
                        return await self._tool_task(args, depth=depth, event_queue=eq,
                                                     directory=(state or {}).get("_cwd", ""),
                                                     conversation_id=str((state or {}).get("conversation_id") or ""))
                    if name in ("tool_memory_set", "tool_memory_get", "tool_memory_search"):
                        return await self._tool_memory(name, args, state)
                    eq = state.get("_event_queue") if state else None
                    if eq and name == "tool_execute":
                        try:
                            return await self._execute_tool_streaming(args, eq, state.get("_on_activity"))
                        except NeedsPermission:
                            raise
                        except Exception as e:
                            logger.warning("tool_execute streaming failed, falling back to sync: %s", e)
                            from app.tools.file_tools import tool_execute as _sync_execute, unwrap
                            result = await asyncio.to_thread(_sync_execute, **args)
                            return unwrap(result)
                    if inspect.iscoroutinefunction(t.fn):
                        result = await t.fn(**args)
                    else:
                        result = await asyncio.to_thread(t.fn, **args)
                    return self._bound_plugin_result(name, result)
                except NeedsPermission as e:
                    mgr = get_perm_mgr()
                    req = mgr.create_request(e.path, e.operation, name, args)
                    eq = state.get("_event_queue") if state else None
                    if eq:
                        eq.put_nowait({
                            "type": "permission_request",
                            "request_id": req.id,
                            "path": e.path,
                            "operation": e.operation,
                            "tool_name": name,
                            "tool_args": args,
                        })
                    if not eq:
                        # 无事件队列（如多 agent 总线路径）时无人能审批，
                        # 直接拒绝而不是永久等待（此前会卡到 supervisor 超时）
                        logger.warning("Permission request denied: no event queue to approve %s", e.path)
                        return _permission_denied_msg(e.operation, e.path, name)
                    decision = await mgr.await_decision(req.id)
                    if decision == "allowed":
                        if e.operation == "command":
                            mgr.add_temp_command_approval(e.path)
                        else:
                            mgr.add_temp_approval(e.path)
                        if eq and name == "tool_execute":
                            try:
                                return await self._execute_tool_streaming(args, eq, state.get("_on_activity"))
                            except NeedsPermission:
                                pass
                            except Exception as e2:
                                logger.warning("tool_execute streaming failed on retry, falling back: %s", e2)
                        if inspect.iscoroutinefunction(t.fn):
                            result = await t.fn(**args)
                        else:
                            result = await asyncio.to_thread(t.fn, **args)
                        return self._bound_plugin_result(name, result)
                    return _permission_denied_msg(e.operation, e.path, name)
                except Exception as e:
                    return f"Error executing {name}: {e}"
        return f"Tool '{name}' not found"

    async def _execute_tool_streaming(self, args: dict, event_queue: asyncio.Queue, on_activity: Callable[[str], None] | None = None) -> str:
        """流式执行shell命令，实时推送输出到事件队列。

        对齐 opencode shell.ts 的单路径语义：Popen 启动子进程 + 后台线程读流，
        经 loop.call_soon_threadsafe 把行推回事件队列——不依赖事件循环自身的
        子进程能力（Windows + uvicorn --reload 下事件循环为 SelectorEventLoop，
        asyncio.create_subprocess_* 会抛 NotImplementedError）。
        """
        command = args.get("command", "")
        timeout = min(args.get("timeout", 300), 600)
        if timeout < 1:
            timeout = 5
        work_dir = args.get("workdir") or args.get("work_dir") or "."

        from app.tools.file_tools import _resolve as _fs_resolve, _kill_process_tree as _kill_tree
        resolved_cwd = _fs_resolve(work_dir)

        from app.permission import get_manager as _get_perm_mgr, NeedsPermission as _NeedsPermission
        mgr = _get_perm_mgr()
        decision = mgr.check(str(resolved_cwd), "execute")
        if decision == "deny":
            return f"Error: access denied to directory '{work_dir}'"
        if decision == "ask":
            raise _NeedsPermission(str(resolved_cwd), "execute", "tool_execute", args)

        # Apply whitelist check (same as filesystem.tool_execute)
        from app.tools.file_tools import _validate_shell_command, _needs_shell
        try:
            _validate_shell_command(command, cwd=str(resolved_cwd), ask=True)
        except ValueError as e:
            return f"Error: {e}"

        # 检查写重定向目标文件权限（>、>> 等），防止 shell 命令绕过文件工具的权限检查
        from app.tools.file_tools import _check_redirect_targets_permission
        try:
            _check_redirect_targets_permission(command, resolved_cwd)
        except (_NeedsPermission, PermissionError):
            raise
        except Exception:
            pass

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        start_time = tmod.time()
        hb_state = {"last": start_time}
        loop = asyncio.get_running_loop()

        def _push(ev: dict) -> None:
            """仅在事件循环线程内执行（经 call_soon_threadsafe 调度），对象非线程安全也安全。"""
            try:
                event_queue.put_nowait(ev)
            except Exception:
                pass

        def _emit_threadsafe(ev: dict) -> None:
            try:
                loop.call_soon_threadsafe(_push, ev)
            except RuntimeError:
                pass  # 循环已关闭（请求已结束/取消），静默丢弃

        def _pump(pipe, storage: list[str], source: str) -> None:
            """工作线程：逐行读管道，落 storage 并推送 tool_output/心跳事件（与旧异步版同语义）。"""
            from app.tools.file_tools import decode_process_output
            try:
                for raw in iter(pipe.readline, b""):
                    decoded = decode_process_output(raw).rstrip()
                    storage.append(decoded)
                    now = tmod.time()
                    if now - hb_state["last"] >= 5:
                        hb_state["last"] = now
                        _emit_threadsafe({
                            "type": "tool_heartbeat",
                            "tool_name": "tool_execute",
                            "elapsed_seconds": int(now - start_time),
                            "command": command[:200],
                        })
                        if on_activity:
                            try:
                                on_activity(f"tool_execute 运行中 ({int(now - start_time)}s)")
                            except Exception:
                                pass
                    _emit_threadsafe({
                        "type": "tool_output",
                        "tool_name": "tool_execute",
                        "source": source,
                        "line": decoded,
                        "elapsed_seconds": int(tmod.time() - start_time),
                    })
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        # 无 shell 语义 → exec 防注入；否则走真实 shell（与 tool_execute 对齐）
        popen_kwargs: dict = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        try:
            if _needs_shell(command):
                process = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(resolved_cwd) if resolved_cwd.is_dir() else None,
                    **popen_kwargs,
                )
            else:
                process = subprocess.Popen(
                    shlex.split(command),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(resolved_cwd) if resolved_cwd.is_dir() else None,
                )
        except Exception as e:
            detail = str(e) or repr(e) or type(e).__name__
            return (
                f"Error starting command: {detail}\n"
                f"Command: {command!r}\n"
                f"Workdir: {str(resolved_cwd) if resolved_cwd.is_dir() else '.'}"
            )

        readers = [
            threading.Thread(target=_pump, args=(process.stdout, stdout_lines, "stdout"), daemon=True),
            threading.Thread(target=_pump, args=(process.stderr, stderr_lines, "stderr"), daemon=True),
        ]
        for th in readers:
            th.start()

        # 事件驱动等待：process.wait() 在线程里阻塞（不依赖事件循环的子进程协程），
        # wait_for 提供超时。相比旧版 poll()+sleep 轮询无 0.2s 延迟地板，
        # 恢复旧实现的即时返回；超时取消后杀树，等待线程随进程退出自然回收。
        timed_out = False
        try:
            await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            # 同步杀整树（taskkill /T /F）：不用 _async_kill_process_tree——
            # 其内部同样走 create_subprocess_exec，selector 循环下会静默失败
            await asyncio.to_thread(_kill_tree, process)
            await asyncio.to_thread(process.wait)
            return f"Error: command timed out after {timeout}s"

        def _join_all() -> None:
            for th in readers:
                th.join(5)

        # 命令已执行完毕，此处绝不能再抛异常（否则调用方会回退同步路径造成二次执行）
        try:
            await asyncio.wait_for(asyncio.to_thread(_join_all), timeout=12)
        except Exception:
            pass

        parts = []
        if stdout_lines:
            parts.append("\n".join(stdout_lines))
        if stderr_lines:
            parts.append(f"[stderr]\n" + "\n".join(stderr_lines))
        output = "\n".join(parts)
        rc = process.returncode or 0
        header = f"Exit code: {rc}"
        if output:
            result = f"{header}\n{output}"
        else:
            result = header
        from app.tools.file_tools import append_cmd_dialect_hint
        return append_cmd_dialect_hint(result)

    def _push_stream_event(self, state: AgentState, event: dict):
        """流式文本增量事件：只进事件队列，不进 steps（避免污染步骤列表）。"""
        eq = state.get("_event_queue")
        if eq:
            try:
                eq.put_nowait(event)
            except Exception:
                pass

    def _assemble_response(self, model: str, response, start: float, state: AgentState | None, push_text: bool = False):
        """记录调用指标并累加 token 用量，可选把非流式全文转为 text_delta 推送。"""
        dur = (tmod.time() - start) * 1000
        usage = getattr(response, "usage", None)
        pt = getattr(usage, "prompt_tokens", 0) if usage else 0
        ct = getattr(usage, "completion_tokens", 0) if usage else 0
        hit, miss = _extract_cache_usage(usage, pt=pt)
        trace("llm.usage", where="assemble", model=model, pt=pt, ct=ct, cache_hit=hit, cache_miss=miss, duration_ms=dur)  # [token trace v7]
        record_model_call(model, prompt_tokens=pt, completion_tokens=ct, duration_ms=dur)
        # 累加本次 invoke 的 token 用量（invoke 前重置），供 assistant 消息结算落库
        if not getattr(self, "_usage_accum", None):
            self._usage_accum = dict(_ZERO_USAGE)
        self._usage_accum["input"] += int(pt or 0)
        self._usage_accum["output"] += int(ct or 0)
        self._usage_accum["cache_read"] += hit
        self._usage_accum["cache_write"] += miss
        logger.info(
            "LLM call | model=%s pt=%d ct=%d cache_hit=%d cache_miss=%d dur=%.0fms",
            model, pt, ct, hit, miss, dur,
        )
        if state is not None and push_text:
            content = getattr(response.choices[0].message, "content", "") or ""
            if content:
                self._push_stream_event(state, {"type": "text_delta", "delta": content})
        return response

    async def _llm_call(self, model: str, messages: list, tool_defs: list, state: AgentState | None = None):
        """调用大语言模型API（流式）并记录调用指标。

        流式把文本增量经 _push_stream_event 实时转发（type=text_delta），同时累积出
        完整 message（含 tool_calls/finish_reason），与原有调用在 _generate 中完全兼容。
        流式建立失败自动回退非流式；流式中断则用已累积内容兜底。
        """
        from types import SimpleNamespace

        start = tmod.time()
        log_prompt("graph.llm_call", messages, model=model, tool_count=len(tool_defs or []))  # [prompt log v1]
        try:
            stream = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=tool_defs,
                api_key=self.api_key,
                api_base=self.api_base,
                temperature=0.1,
                max_tokens=settings.llm_max_tokens,
                timeout=500,
                num_retries=2,
                stream=True,
                stream_options={"include_usage": True},
                cache_prompt=True,
            )
        except Exception as e:
            logger.warning("LLM stream init failed, falling back to non-stream: %s", e)
            try:
                response = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    tools=tool_defs,
                    api_key=self.api_key,
                    api_base=self.api_base,
                    temperature=0.1,
                    max_tokens=settings.llm_max_tokens,
                    timeout=500,
                    num_retries=2,
                    cache_prompt=True,
                )
            except Exception as exc:
                dur = (tmod.time() - start) * 1000
                trace("llm.usage", where="error", model=model, pt=0, ct=0, duration_ms=dur)  # [token trace v7]
                record_model_call(model, duration_ms=dur)
                raise exc
            return self._assemble_response(model, response, start, state, push_text=True)

        text_chunks: list[str] = []
        tool_slots: dict[int, dict] = {}
        finish_reason = None
        usage = None
        try:
            async for chunk in stream:
                u = getattr(chunk, "usage", None)
                if u is not None:
                    usage = u
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                choice = choices[0]
                fr = getattr(choice, "finish_reason", None)
                if fr:
                    finish_reason = fr
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                c = getattr(delta, "content", None)
                if c:
                    text_chunks.append(c)
                    if state is not None:
                        self._push_stream_event(state, {"type": "text_delta", "delta": c})
                for tc in (getattr(delta, "tool_calls", None) or []):
                    idx = getattr(tc, "index", None) or 0
                    slot = tool_slots.setdefault(
                        idx,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if getattr(tc, "id", None):
                        slot["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        if getattr(fn, "name", None):
                            slot["function"]["name"] += fn.name
                        if getattr(fn, "arguments", None):
                            slot["function"]["arguments"] += fn.arguments
        except Exception:
            # 流式中断 → 用已累积内容兜底（不再重试）
            logger.warning("LLM stream interrupted, using accumulated content", exc_info=True)

        content = "".join(text_chunks)
        dur = (tmod.time() - start) * 1000
        pt = getattr(usage, "prompt_tokens", 0) if usage else 0
        ct = getattr(usage, "completion_tokens", 0) if usage else 0
        hit, miss = _extract_cache_usage(usage, pt=int(pt or 0))
        trace("llm.usage", where="invoke", model=model, pt=int(pt or 0), ct=int(ct or 0), cache_hit=hit, cache_miss=miss, duration_ms=dur)  # [token trace v7]
        record_model_call(model, prompt_tokens=int(pt or 0), completion_tokens=int(ct or 0), duration_ms=dur)
        if not getattr(self, "_usage_accum", None):
            self._usage_accum = dict(_ZERO_USAGE)
        self._usage_accum["input"] += int(pt or 0)
        self._usage_accum["output"] += int(ct or 0)
        self._usage_accum["cache_read"] += hit
        self._usage_accum["cache_write"] += miss
        logger.info(
            "LLM call | model=%s pt=%d ct=%d cache_hit=%d cache_miss=%d dur=%.0fms",
            model, int(pt or 0), int(ct or 0), hit, miss, dur,
        )

        tool_calls = None
        if tool_slots:
            tool_calls = [
                SimpleNamespace(
                    id=slot["id"],
                    type=slot["type"],
                    function=SimpleNamespace(name=slot["function"]["name"], arguments=slot["function"]["arguments"]),
                )
                for _, slot in sorted(tool_slots.items())
            ]
        msg = SimpleNamespace(content=content, tool_calls=tool_calls)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish_reason)], usage=usage)

    async def _generate(self, state: AgentState) -> dict:
        """调用LLM生成回答，支持多轮工具调用。"""
        _gen_start = tmod.time()
        self._usage_accum = dict(_ZERO_USAGE)
        dedup = ToolResultDedup()
        from app.context.compaction import ContextCompactor
        compactor = ContextCompactor(
            model=settings.summarization_model or self.model,
            api_key=settings.summarization_api_key or settings.llm_api_key,
            api_base=settings.summarization_api_base or settings.llm_api_base,
            threshold=compaction_threshold_tokens(),
            tail_turns=settings.context_tail_turns,
            preserve_recent_tokens=settings.context_preserve_recent_tokens,
        )
        self._push_event(state, {"type": "step_start", "step_id": "generate", "name": "生成回答", "status": "running"})

        context_text = ""
        if state["context"]:
            context_parts = [
                f"[Source {i+1}]: {c['content']}"
                for i, c in enumerate(state["context"])
            ]
            context_text = "\n\n".join(context_parts)
        # [token 优化 v2] system 保持完全稳定 → 最大化 DeepSeek 前缀缓存命中（命中按 0.1x 计费）
        # RAG 检索结果改放 user 消息前缀（见下方 user 消息构建），避免 system 每次变化导致缓存整体失效。
        full_system_prompt = self.system_prompt
        # [会话目录] 本会话绑定的工作目录追加为 system 末尾（同一目录内保持稳定）
        if state.get("_cwd"):
            full_system_prompt += (
                "\n\n[会话工作目录]\n"
                f"Current session working directory: {state['_cwd']}\n"
                "Relative paths in file tools (tool_ls/read_file/write_file/append_file/edit_file/"
                "glob/grep/execute) resolve under this directory. Files written here are allowed without permission."
            )

        # [token 优化 v5] 按需挂载：首轮按问题关键词筛选工具 schema
        tool_defs = self._build_tool_defs(state.get("question", ""))

        messages = [
            {"role": "system", "content": full_system_prompt},
        ]
        if state.get("history"):
            messages.extend(state["history"])

        # Build user content: text only or multimodal if files attached
        # [token 优化 v2] RAG 上下文改放 user 消息前缀（system 保持稳定 → 命中前缀缓存）
        user_question = (
            f"Retrieved Context:\n{context_text}\n\n---\n\n{state['question']}"
            if context_text else state["question"]
        )
        user_files = state.get("files", [])
        if user_files:
            user_content: list[dict] = [{"type": "text", "text": user_question}]
            for f in user_files:
                if f.get("mime_type", "").startswith("image/"):
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{f['mime_type']};base64,{f['data']}"},
                    })
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": user_question})

        model = state.get("model") or self.model
        if "/" not in model:
            if self.api_base and "deepseek" in self.api_base:
                model = f"deepseek/{model}"
            elif self.api_base and "openai" in self.api_base:
                model = f"openai/{model}"

        # [token 优化 v4] 压缩优先于硬截断：首轮若已超压缩阈值，先 LLM 压缩（保留事实摘要），
        # 避免直接丢弃旧历史导致模型失忆重做（重做比压缩更贵）。截断仅作最后兜底。
        # 工具循环内每轮已有同款 压缩→截断 闭环，此处在入口补齐，覆盖多轮对话 history 场景。
        if compactor.should_compact(messages):
            self._push_event(state, {"type": "step_start", "step_id": "compaction", "name": "压缩上下文", "status": "running"})
            old_count = len(messages)
            messages = await compactor.compact(messages)
            messages = sanitize_tool_messages(messages)
            if state.get("_task"):
                state["_task"].record_compaction()
            self._push_event(state, {"type": "step_end", "step_id": "compaction", "name": "压缩上下文", "status": "completed", "detail": f"{old_count} 条消息压缩为 {len(messages)} 条"})
        messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=usable_context_tokens(), reserve_tokens=0, tool_defs=tool_defs))
        trace_messages("graph.entry_ready", messages, tool_defs=tool_defs)  # [token trace v7]

        response = await self._llm_call(model, messages, tool_defs, state=state)
        msg = response.choices[0].message
        # P4: 读取并归一化 finish_reason（对齐 opencode FinishReason：tool-calls/unknown 不算完成）
        finish_reason = _normalize_finish_reason(getattr(response.choices[0], "finish_reason", None))

        # 硬兜底：单次请求内最多 LLM 调用轮数（每轮 = 一次完整 LLM 调用）
        max_tool_rounds = settings.max_tool_rounds
        # 主步骤上限（对齐 opencode agent.steps）：生效上限 = min(MAX_STEPS, MAX_TOOL_ROUNDS)
        effective_max_steps = min(max_tool_rounds, max(1, settings.max_steps))
        # Doom-loop：连续相同指纹 N 轮注入提示；升级到 doom_loop_max_strikes 次后强制收尾
        doom_threshold = max(2, settings.doom_loop_threshold)
        doom_max_strikes = max(1, settings.doom_loop_max_strikes)
        doom_fingerprints: list[str] = []
        doom_strikes = 0
        steps_prompt_injected = False
        rounds = 0
        tool_calls_count = 0
        # [token 优化 v5] 已使用工具集合：每轮重挂载时保留，避免模型想复用却被移除
        used_tools: set[str] = set()
        while (
            msg.tool_calls or finish_reason == "tool-calls"
        ) and rounds < max_tool_rounds:
            rounds += 1

            # Compaction: compress old messages when context grows large
            # （先回溯清理旧工具输出，再判断是否触发压缩）
            trace_messages("graph.round_start", messages)  # [token trace v7]
            messages = prune_tool_outputs(
                messages,
                protect_tokens=prune_protect_tokens(),
                minimum_tokens=prune_minimum_tokens(),
                tail_turns=settings.context_tail_turns,
            )
            trace_messages("graph.pre_compact", messages, threshold=compaction_threshold_tokens())  # [token trace v7]
            if compactor.should_compact(messages):
                self._push_event(state, {"type": "step_start", "step_id": "compaction", "name": "压缩上下文", "status": "running"})
                old_count = len(messages)
                messages = await compactor.compact(messages)
                messages = sanitize_tool_messages(messages)
                if state.get("_task"):
                    state["_task"].record_compaction()
                self._push_event(state, {"type": "step_end", "step_id": "compaction", "name": "压缩上下文", "status": "completed", "detail": f"{old_count} 条消息压缩为 {len(messages)} 条"})

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })

            tool_tasks = []
            tool_metas = []
            early_results: dict[str, str] = {}
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                # [token 优化 v5] 记录已使用工具 → 下轮重挂载时保留
                used_tools.add(tool_name)
                args = parse_tool_args(tc.function.arguments)
                if args is None:
                    early_results[tc.id] = f"Error parsing arguments for '{tool_name}': 参数不是合法 JSON 且自动修复失败（已按空参数处理，请重新提交完整参数）"
                    continue

                # Dedup: 仅对只读且幂等的工具复用结果。
                # 非只读工具（写/执行）和非确定性工具（weather/时间/网络/HTTP 等）跳过缓存，
                # 避免同轮内相同参数第二次调用返回过期/陈旧结果。
                if not dedup.should_dedup(tool_name) or tool_name not in _DEDUP_READONLY_TOOLS:
                    self._push_event(state, {"type": "tool_start", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "running", "tool_name": tool_name, "tool_args": args})
                    tool_tasks.append(self._execute_tool(tool_name, args, state))
                    tool_metas.append((tc.id, tool_name, None))
                    continue

                dedup_key = dedup.make_key(tool_name, args)
                cached = dedup.get(dedup_key)
                if cached is not None:
                    early_results[tc.id] = cached
                    self._push_event(state, {"type": "tool_start", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "running", "tool_name": tool_name, "tool_args": args})
                    self._push_event(state, {"type": "tool_end", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "completed", "tool_name": tool_name, "tool_result": cached[:500]})
                    continue

                self._push_event(state, {"type": "tool_start", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "running", "tool_name": tool_name, "tool_args": args})
                tool_tasks.append(self._execute_tool(tool_name, args, state))
                tool_metas.append((tc.id, tool_name, dedup_key))

            if tool_tasks:
                tool_results = await asyncio.gather(*tool_tasks, return_exceptions=True)
            else:
                tool_results = []

            for (tc_id, tool_name, dkey), result in zip(tool_metas, tool_results):
                if isinstance(result, Exception):
                    result = f"Error executing {tool_name}: {result}"
                result_str = str(result)
                if dkey is not None:
                    dedup.set(dkey, result_str)
                early_results[tc_id] = result_str

            # 变更类工具（写/编辑/执行/插件生成器等）执行后清空去重缓存，
            # 避免后续 read_file 命中旧缓存返回陈旧内容（写入→读取验证失效）
            if any(name not in _DEDUP_READONLY_TOOLS for _, name, _ in tool_metas):
                dedup.clear()

            for tc in msg.tool_calls:
                tc_id = tc.id
                result_str = early_results.get(tc_id, f"Error: no result for tool call {tc_id}")
                bounded_result = bound_tool_output(result_str, tc.function.name)
                tool_name = tc.function.name
                self._push_event(state, {"type": "tool_end", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "completed", "tool_name": tool_name, "tool_result": bounded_result[:500]})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "tool_name": tool_name,
                    "content": bounded_result,
                })

            # TaskState 进度跟踪：每轮 +1 step，并按本轮工具调用数累加 tool_calls_count
            tool_calls_count += len(msg.tool_calls)
            if state.get("_task"):
                state["_task"].increment_step()
                state["_task"].increment_tool_calls(len(msg.tool_calls))

            # Doom-loop 检测：同一组工具调用指纹连续重复 ≥ threshold 轮 → 注入策略变更提示；
            # 首次提示后仍连续重复（升级到 doom_loop_max_strikes）→ 强制收尾（注入 MAX_STEPS_PROMPT + 禁用工具）
            fp = "|".join(
                sorted(f"{tc.function.name}:{tc.function.arguments}" for tc in msg.tool_calls)
            )
            doom_fingerprints.append(fp)
            if len(doom_fingerprints) >= doom_threshold and len(set(doom_fingerprints[-doom_threshold:])) == 1:
                doom_strikes += 1
                if doom_strikes >= doom_max_strikes:
                    logger.warning(
                        "Doom loop persisted (%d strikes), forcing structured summary: %s",
                        doom_strikes, fp[:120],
                    )
                    messages.append({"role": "assistant", "content": MAX_STEPS_PROMPT})
                    steps_prompt_injected = True
                    self._push_event(state, {"type": "step_end", "step_id": "doom_loop", "name": "重复工具调用升级", "status": "completed", "detail": "已强制收尾总结"})
                else:
                    logger.warning("Doom loop detected: %d consecutive identical tool calls (%s)", doom_threshold, fp[:120])
                    messages.append({"role": "user", "content": DOOM_LOOP_PROMPT})
                    self._push_event(state, {"type": "step_end", "step_id": "doom_loop", "name": "检测到重复工具调用", "status": "completed", "detail": "已注入策略变更提示"})
                doom_fingerprints.clear()

            # MAX_STEPS：达到生效上限前的最后一轮注入收尾提示（对齐 opencode prompt.ts:1281，
            # 以 assistant 角色消息注入，模型据此收尾总结）
            if not steps_prompt_injected and rounds >= effective_max_steps:
                steps_prompt_injected = True
                messages.append({"role": "assistant", "content": MAX_STEPS_PROMPT})

            # [token 优化 v5] 每轮按需重挂载（核心常驻 + 意图命中 + 已使用保留），schema 固定开销大降
            # [token 优化 P9] 先构建本轮 schema 再截断，预算需扣除 tools 序列化开销（tool_defs 移到此处理）
            tool_defs = self._build_tool_defs(state.get("question", ""), used_tools)
            # MAX_STEPS 注入后不再允许继续调用工具（对齐 opencode max-steps.ts 的 disable-tools 语义）
            final_tool_defs = None if steps_prompt_injected else tool_defs
            messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=usable_context_tokens(), reserve_tokens=0, tool_defs=final_tool_defs))
            trace_messages("graph.round_ready", messages, tool_defs=final_tool_defs)  # [token trace v7]
            response = await self._llm_call(model, messages, final_tool_defs, state=state)
            msg = response.choices[0].message
            finish_reason = _normalize_finish_reason(getattr(response.choices[0], "finish_reason", None))

        record_model_call(
            model, duration_ms=(tmod.time() - _gen_start) * 1000,
            tool_rounds=rounds, tool_calls=tool_calls_count,
        )
        trace("graph.finish", rounds=rounds, tool_calls=tool_calls_count, duration_ms=(tmod.time() - _gen_start) * 1000)  # [token trace v7]

        # If tool calls remain (max rounds reached) or content is empty, force a final answer
        if msg.tool_calls:
            logger.warning("Max tool rounds (%d) reached, executing final batch and forcing answer", max_tool_rounds)
            # Must include tool_calls in assistant message for DeepSeek/OpenAI compatibility
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })
            tool_tasks = []
            tool_metas = []
            for tc in msg.tool_calls:
                args = parse_tool_args(tc.function.arguments)
                if args is None:
                    args = {}
                self._push_event(state, {"type": "tool_start", "step_id": f"tool_{tc.function.name}", "name": f"调用工具: {tc.function.name}", "status": "running", "tool_name": tc.function.name, "tool_args": args})
                tool_tasks.append(self._execute_tool(tc.function.name, args, state))
                tool_metas.append((tc.id, tc.function.name))

            tool_results = await asyncio.gather(*tool_tasks, return_exceptions=True)

            for (tc_id, tool_name), result in zip(tool_metas, tool_results):
                if isinstance(result, Exception):
                    result = f"Error executing {tool_name}: {result}"
                result_str = str(result)
                bounded_result = bound_tool_output(result_str, tool_name)
                self._push_event(state, {"type": "tool_end", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "completed", "tool_name": tool_name, "tool_result": bounded_result[:500]})
                messages.append({"role": "tool", "tool_call_id": tc_id, "tool_name": tool_name, "content": bounded_result})
            # [token 优化 P8] 强制收尾路径补齐"清理→压缩→截断"闭环：此前仅截断，
            # 且截断基于低估估算可能不触发（实测收尾轮裸发 25,779 超 usable 23,808）。
            # 与主循环保持同款处理，避免收尾调用成为单请求内最大单次 pt。
            trace_messages("graph.final_round_start", messages, tool_defs=None)  # [token trace v8]
            messages = prune_tool_outputs(
                messages,
                protect_tokens=prune_protect_tokens(),
                minimum_tokens=prune_minimum_tokens(),
                tail_turns=settings.context_tail_turns,
            )
            if compactor.should_compact(messages):
                self._push_event(state, {"type": "step_start", "step_id": "compaction", "name": "压缩上下文", "status": "running"})
                old_count = len(messages)
                messages = await compactor.compact(messages)
                messages = sanitize_tool_messages(messages)
                if state.get("_task"):
                    state["_task"].record_compaction()
                self._push_event(state, {"type": "step_end", "step_id": "compaction", "name": "压缩上下文", "status": "completed", "detail": f"{old_count} 条消息压缩为 {len(messages)} 条"})
            messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=usable_context_tokens(), reserve_tokens=0))
            trace_messages("graph.final_round_ready", messages, tool_defs=None)  # [token trace v8]
            # 对齐 opencode max-steps 语义：达到上限后工具禁用，仅注入收尾总结提示（assistant 角色）
            messages.append({"role": "assistant", "content": MAX_STEPS_PROMPT})
            response = await self._llm_call(model, messages, None, state=state)
            msg = response.choices[0].message
            finish_reason = _normalize_finish_reason(getattr(response.choices[0], "finish_reason", None))
        if msg.tool_calls:
            # 收尾调用已禁用工具（tools=None），理论上不应返回 tool_calls；
            # 若模型仍输出，其 tool_calls 不会进入 answer，仅告警记录便于排查
            logger.warning(
                "Forced final LLM call returned %d tool_calls despite tools disabled (rounds=%d)",
                len(msg.tool_calls), rounds,
            )
        if not (msg.content or "").strip():
            # Last resort: LLM still returned empty, use a summary
            msg.content = "任务已完成，请查看结果。"

        # P4: finish_reason 收尾语义（对齐 opencode prompt.ts:1301-1308 / processor.ts）
        # length → 输出被截断，答案不完整，追加提示不静默
        # content-filter → 内容被 Provider 过滤，视为错误暴露
        answer = msg.content or ""
        gen_dur = (tmod.time() - _gen_start) * 1000
        if finish_reason == "length":
            answer = answer + "\n\n⚠️ 输出因达到 token 上限被截断，内容可能不完整。"
            self._push_event(state, {"type": "step_end", "step_id": "generate", "name": "生成回答", "status": "completed", "detail": "完成（输出被截断 length）", "duration_ms": round(gen_dur, 1)})
        elif finish_reason == "content-filter":
            answer = "模型回答被内容安全策略拦截，未返回完整内容。请调整提问方式或拆分内容后重试。"
            self._push_event(state, {"type": "step_end", "step_id": "generate", "name": "生成回答", "status": "error", "detail": "内容被过滤", "duration_ms": round(gen_dur, 1)})
        else:
            self._push_event(state, {"type": "step_end", "step_id": "generate", "name": "生成回答", "status": "completed", "detail": f"完成（{rounds} 轮工具调用）" if rounds else "完成", "duration_ms": round(gen_dur, 1)})
        return {
            "answer": answer,
            "messages": [AIMessage(content=answer)],
            "model": model,
            "finish": finish_reason,
            "tokens": dict(self._usage_accum),
        }

    def _build_graph(self):
        """构建LangGraph状态图，定义检索、重排序和生成的流程。"""
        builder = StateGraph(AgentState)
        builder.add_node("retrieve", self._retrieve)
        if self.reranker:
            builder.add_node("rerank", self._rerank)
        builder.add_node("generate", self._generate)
        builder.set_entry_point("retrieve")
        if self.reranker:
            builder.add_edge("retrieve", "rerank")
            builder.add_edge("rerank", "generate")
        else:
            builder.add_edge("retrieve", "generate")
        builder.add_edge("generate", END)
        return builder.compile()

    async def refresh_tools(self):
        """刷新工具列表和系统提示，用于热更新技能和插件。

        加锁避免与并发请求中的 graph 使用竞态（技能/插件切换时原子替换）。
        """
        async with self._refresh_lock:
            self.tools = []
            self.tools.extend(create_filesystem_tools())
            if self.skill_loader:
                self.tools.extend(create_skill_tools(self.skill_loader))
            if self.plugin_loader:
                self.tools.extend(create_plugin_tools(self.plugin_loader))
            self.rebuild_system_prompt()
            self.graph = self._build_graph()

    async def invoke(self, question: str, model: str | None = None, history: list[dict] | None = None, use_vector_db: bool = False, files: list[dict] | None = None, event_queue: asyncio.Queue | None = None, conversation_id: str = "", on_activity: Callable[[str], None] | None = None, directory: str = "", task_depth: int = 0) -> dict:
        """执行完整的RAG流程，返回回答和相关源。

        参数:
            conversation_id: 对话ID，传入时会自动创建并跟踪 TaskState。
            directory: 会话绑定的工作目录（opencode ctx.directory）。非空时写入
                system prompt，并把该目录挂为本次执行的文件作用域（相对路径基准
                + 可写权限），执行结束自动解除。
            task_depth: 当前在子 Agent 委派链中的深度（tool_task 嵌套时逐层 +1），
                用于 SUBAGENT_DEPTH 深度护栏。
        """
        # 可选：集成 TaskState 跟踪（当 conversation_id 不为空时）
        task = None
        if conversation_id:
            from app.context.task_state import TaskState
            task = TaskState(conversation_id=conversation_id)
            task.save()

        from app.permission import set_session_workspace, reset_session_workspace
        ws_token = set_session_workspace(directory) if directory else None
        try:
            state = AgentState(
                messages=[HumanMessage(content=question)],
                question=question,
                context=[],
                answer="",
                sources=[],
                model=model,
                history=history or [],
                use_vector_db=use_vector_db,
                files=files or [],
                steps=[],
                tokens=dict(_ZERO_USAGE),
                finish="stop",
                _event_queue=event_queue,
                _on_activity=on_activity,
                _task=task,
                _cwd=directory or "",
                _task_depth=max(0, int(task_depth or 0)),
                conversation_id=conversation_id,
            )
            try:
                result = await self.graph.ainvoke(state)
            except Exception as e:
                if task:
                    task.mark_failed(str(e))
                raise

            if task:
                task.mark_completed()

            return {
                "answer": result.get("answer", ""),
                "sources": result.get("sources", []),
                "steps": result.get("steps", []),
                "messages": result.get("messages", []),
                "task": task.to_dict() if task else {},
                "model": result.get("model") or self.model,
                "finish": result.get("finish", "stop"),
                "tokens": result.get("tokens") or dict(_ZERO_USAGE),
                "cost": result.get("cost"),
            }
        finally:
            if ws_token is not None:
                reset_session_workspace(ws_token)
