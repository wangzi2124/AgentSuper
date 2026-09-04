"""拆分模块 `base`（含 RAGAgentBase）。

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

from collections import OrderedDict

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
# ── 跨子模块依赖（自动生成）──
from .constants import _TASK_TOOL_SCHEMA
from .state import AgentState
from .state import _ZERO_USAGE
logger = logging.getLogger(__name__)
# ── 类分块（verbatim，继承链切片）──
class RAGAgentBase:
    def __init__(
        self,
        retriever: Retriever,
        skill_loader: SkillLoader | None = None,
        plugin_loader: PluginLoader | None = None,
        reranker: Reranker | None = None,
        custom_tools: CustomToolStore | None = None,  # [token 优化 v6] 前端添加的自定义工具/固定工具
        memory=None,  # [opencode memory] 共享记忆管理器（runtime.py 注入）
        voice_service=None,  # [语音] 本地 Qwen3-TTS 服务（runtime.py 按 VOICE_TTS_ENABLED 注入）
    ):
        self.retriever = retriever
        self.reranker = reranker
        self.skill_loader = skill_loader
        self.plugin_loader = plugin_loader
        self.custom_tools = custom_tools
        self.memory = memory
        self.voice_service = voice_service
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

        # [语音] 主 Agent 语音合成/转写工具：仅当注入了启用状态的本地 Qwen3-TTS 服务。
        # fn 为占位，实际执行在 _execute_tool 特判（与 tool_memory_* 同机制）。
        if voice_service is not None and getattr(voice_service, "enabled", False):
            self.tools.append(ToolDef(
                name="tool_tts_synthesize",
                description=(
                    "将一段文本合成为语音文件（本地 Qwen3-TTS 预设音色），保存到 data/generated/ 下，"
                    "返回文件路径。用于用户要求生成语音/配音/朗读内容时，把答复文本转成可下载/可播放的 wav。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要合成的文本（中文优先，建议简洁完整句）"},
                        "speaker": {"type": "string", "description": "可选音色：Vivian/Serena/Uncle_fu/Dylan/Eric/Ryan/Aiden/Ono_anna/Sohee"},
                        "language": {"type": "string", "description": "语言：Auto/Chinese/English/Japanese/Korean/...（默认 Auto）"},
                        "instruct": {"type": "string", "description": "可选风格/情绪指令，如 'speak happily'（英文）"},
                    },
                    "required": ["text"],
                },
                fn=self._voice_tool_placeholder,
            ))
            self.tools.append(ToolDef(
                name="tool_voice_transcribe",
                description=(
                    "将一段本地音频文件转写为文本（Whisper ASR）。"
                    "用于用户提供了音频文件（.wav/.mp3 等）并要求转成文字/听懂内容时。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "audio_path": {"type": "string", "description": "本地音频文件路径（绝对路径或相对工作目录）"},
                    },
                    "required": ["audio_path"],
                },
                fn=self._voice_tool_placeholder,
            ))

        # 子 Agent 消息总线（runtime.py 注入）：供 tool_task 委派使用
        self.task_bus: object | None = None

        self.system_prompt = build_system_prompt_no_kb(
            skill_loader or SkillLoader(""),
            plugin_loader or PluginLoader(""),
            include_filesystem=True,
            has_memory=memory is not None,
            has_voice=voice_service is not None and getattr(voice_service, "enabled", False),
        )

        self._usage_accum: dict[str, int] = dict(_ZERO_USAGE)
        # 工具热更新锁：refresh_tools 重建 graph 期间与并发请求互斥
        self._refresh_lock = asyncio.Lock()
        # [token 优化 · 前缀缓存 v15] 会话级已挂载工具名缓存（conversation_id → 有序工具名）。
        # 同一会话内只增不减（新命中工具追加到末尾），使 [system + tools] 前缀跨请求字节稳定，
        # DeepSeek 前缀缓存命中（0.1x 计费 + TTFT 下降）。跨会话并发访问 → 用独立锁。
        self._conv_tool_cache: dict[str, list[str]] = {}
        self._conv_tool_cache_lock = threading.Lock()
        # 前缀缓存收益上限：缓存工具名快照的最大会话数，超限淘汰最旧（LRU 语义简化版）
        self._conv_tool_cache_max = 200

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
            has_voice=getattr(self, "voice_service", None) is not None
            and getattr(getattr(self, "voice_service", None), "enabled", False),
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
            + (
                "Voice/TTS: use tool_tts_synthesize to turn reply text into a downloadable .wav "
                "(saved to data/generated); tool_voice_transcribe to transcribe a local audio file to text."
                "\n\n"
                if getattr(self, "voice_service", None) is not None
                and getattr(getattr(self, "voice_service", None), "enabled", False)
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
        (("语音", "声音", "配音", "克隆", "合成", "朗读", "voice", "audio", "speech"),
         ("plugin_voice-clone_", "tool_tts_synthesize", "tool_voice_transcribe")),
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
    def _build_tool_defs(self, question: str = "", used_names: set | None = None, conversation_id: str = "") -> list[dict] | None:
        """[token 优化 v5+v15] 按需挂载 OpenAI 工具定义，会话内只增不减。

        system prompt 只列常驻工具名 + 一行 skill 提示（[token 优化 v10]），技能清单与
        描述依赖此处按意图把 load_skill_* schema 挂载给 LLM；此处只发本轮可能用到的
        schema：核心文件工具常驻 + 意图关键词命中 + 已使用工具保留。
        schema 固定开销从 8-12K 降到 2-4K。若模型调用了未挂载工具，
        _execute_tool 仍可执行（self.tools 全量），下一轮该工具自动保留。

        [token 优化 v15 · 前缀缓存] 同一会话（conversation_id）内缓存已挂载工具名，
        **只增不减**（新命中追加到末尾）：使「system + tools」前缀跨请求字节稳定 →
        DeepSeek 前缀缓存命中（0.1x 计费 + TTFT 下降），避免旧实现每次按问题意图
        重建 schema 导致前缀漂移、缓存频繁失效。无 conversation_id（一次性调用）
        时保持原状态按意图即时筛选，不污染共享缓存。技能/插件热更新移除的工具
        自动跳过（从缓存剔除，前缀在管理员操作后才会变，可接受）。
        """
        if not self.tools:
            return None
        used = used_names or set()
        q = (question or "").lower()
        # [token 优化 v6] 固定（pin）工具集合只读一次，避免循环内反复读 pinned_tools.json
        pinned = self._pinned_tool_names()
        by_name = {t.name: t for t in self.tools}

        # 本轮"想要"的工具集：常驻 + 固定 + 已使用 + 意图命中
        wanted: set[str] = set()
        for t in self.tools:
            if t.name in pinned:
                wanted.add(t.name)
                continue
            if t.name in used:
                wanted.add(t.name)
                continue
            if t.name.startswith(self._CORE_TOOL_PREFIXES):
                wanted.add(t.name)
                continue
            if self._tool_matches_intent(t, q):
                wanted.add(t.name)

        key = conversation_id or ""
        if key:
            with self._conv_tool_cache_lock:
                mounted = self._conv_tool_cache.get(key)
                if mounted is None:
                    # 会话首个请求：按 self.tools 注册序播种（确定性、与旧行为一致）
                    mounted = [t.name for t in self.tools if t.name in wanted]
                    self._conv_tool_cache[key] = mounted
                    # 简单淘汰最旧，防止多会话无限膨胀（LRU 简化版）
                    if len(self._conv_tool_cache) > self._conv_tool_cache_max:
                        oldest = next(iter(self._conv_tool_cache))
                        if oldest != key:
                            del self._conv_tool_cache[oldest]
                else:
                    # 追加本轮新命中（仅尾部追加 → 前缀不变）
                    existing = set(mounted)
                    for t in self.tools:
                        if t.name not in existing and t.name in wanted:
                            mounted.append(t.name)
                            existing.add(t.name)
                names = list(mounted)
        else:
            # 无会话上下文：按注册序即时筛选（不写共享缓存）
            names = [t.name for t in self.tools if t.name in wanted]

        # 解析为 schema；工具已被热更新移除的名字跳过
        return [by_name[n].to_openai_tool() for n in names if n in by_name]
    def _remember_conversation_tools(self, conversation_id: str, used_names: set | str) -> None:
        """把本请求实际用到的工具名并入会话挂载缓存（只增不减，尾部追加）。

        [token 优化 v15] 与 _build_tool_defs 的会话级缓存配合：模型可能调用未挂载
        工具（_execute_tool 全量可执行），收尾时把 used 并入缓存，下一次同会话请求
        的 schema 自动带上，且前缀只在尾部增长 → DeepSeek 前缀缓存继续命中。
        """
        if not conversation_id:
            return
        names = used_names if isinstance(used_names, (set, list)) else {used_names}
        names = {str(n) for n in names if n}
        if not names:
            return
        with self._conv_tool_cache_lock:
            mounted = self._conv_tool_cache.get(conversation_id)
            if mounted is None:
                # 未走 _build_tool_defs 的会话（理论上不会），仅记录 used，无注册序可用
                self._conv_tool_cache[conversation_id] = [n for n in self.tools if n.name in names]
                return
            existing = set(mounted)
            # 按 self.tools 注册序追加，保证与播种/意图追加一致的确定性顺序
            for t in self.tools:
                if t.name not in existing and t.name in names:
                    mounted.append(t.name)
                    existing.add(t.name)
            if len(self._conv_tool_cache) > self._conv_tool_cache_max:
                oldest = next(iter(self._conv_tool_cache))
                if oldest != conversation_id:
                    del self._conv_tool_cache[oldest]

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

__all__ = ['RAGAgentBase']
