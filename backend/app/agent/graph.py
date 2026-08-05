import asyncio
import json
import logging
import shlex
import time as tmod
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Callable, TypedDict

logger = logging.getLogger(__name__)

WORKSPACE = Path(__file__).resolve().parents[2]

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
from app.context.budget import usable_context_tokens, compaction_threshold_tokens

# 读取类工具：只读文件/目录状态，结果可被后续写操作改变，因此缓存仅在"未发生写操作"时有效
_DEDUP_READONLY_TOOLS = {"tool_ls", "tool_read_file", "tool_glob", "tool_grep"}

import litellm
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END
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
from app.monitor import record_model_call
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


_ZERO_USAGE = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}


class RAGAgent:
    """RAG（检索增强生成）代理，负责协调检索、重排序和生成回答的流程。"""

    def __init__(
        self,
        retriever: Retriever,
        skill_loader: SkillLoader | None = None,
        plugin_loader: PluginLoader | None = None,
        reranker: Reranker | None = None,
    ):
        self.retriever = retriever
        self.reranker = reranker
        self.skill_loader = skill_loader
        self.plugin_loader = plugin_loader
        self.model = settings.llm_model
        self.api_key = settings.llm_api_key
        self.api_base = settings.llm_api_base

        self.tools: list[ToolDef] = []
        self.tools.extend(create_filesystem_tools())
        if skill_loader:
            self.tools.extend(create_skill_tools(skill_loader))
        if plugin_loader:
            self.tools.extend(create_plugin_tools(plugin_loader))

        self.system_prompt = build_system_prompt_no_kb(
            skill_loader or SkillLoader(""),
            plugin_loader or PluginLoader(""),
            include_filesystem=True,
        )

        self._usage_accum: dict[str, int] = dict(_ZERO_USAGE)

        self.graph = self._build_graph()

    def rebuild_system_prompt(self):
        """仅重建系统提示词（不重建图），用于工作区/技能/插件变化后的热更新。

        工作区列表由 build_system_prompt_no_kb 动态读取权限管理器，无需重启。
        """
        self.system_prompt = build_system_prompt_no_kb(
            self.skill_loader or SkillLoader(""),
            self.plugin_loader or PluginLoader(""),
            include_filesystem=True,
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
            eq.put_nowait(event)
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

        if self.retriever.is_empty or not state.get("use_vector_db", True):
            reason = "知识库为空" if self.retriever.is_empty else "已禁用向量检索"
            dur = (tmod.time() - start) * 1000
            self._push_event(state, {"type": "step_end", "step_id": "retrieve", "name": "检索中", "status": "completed", "detail": reason, "duration_ms": round(dur, 1)})
            return {"context": [], "sources": []}

        import functools
        try:
            results = await asyncio.to_thread(
                functools.partial(self.retriever.invoke, state["question"], k=5)
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
            "\n\nYou have access to built-in filesystem tools (tool_ls, tool_read_file, tool_write_file, tool_append_file, tool_edit_file, tool_glob, tool_grep, tool_execute) for reading/writing files and running shell commands."
            "\nYou also have access to skill tools (load_skill_*) and plugin tools."
            "\nIf the user asks to create/edit/manipulate documents (Word, PDF, PPT, Excel), generate visual designs, build web pages, or use other specialized capabilities, call the relevant skill or plugin tool to get instructions first."
            "\n\nCharacter Analysis (for novels, scripts, or documents with dialogues):"
            "\n- plugin_character-analysis_tool_list_characters(): List all characters and their dialogue counts."
            "\n- plugin_character-analysis_tool_get_character_dialogues(character_name, limit): Get all dialogues spoken by a character."
            "\n- plugin_character-analysis_tool_analyze_character_interactions(character_name): Find characters who appear in same chapters."
            "\n\n" + LONG_CONTENT_FILE_RULE
        )

    def _build_tool_defs(self) -> list[dict] | None:
        """构建OpenAI格式的工具定义列表。"""
        if not self.tools:
            return None
        return [t.to_openai_tool() for t in self.tools]

    async def _execute_tool(self, name: str, args: dict, state: dict | None = None) -> str:
        """执行指定的工具函数，处理权限检查和错误。"""
        for t in self.tools:
            if t.name == name:
                try:
                    eq = state.get("_event_queue") if state else None
                    if eq and name == "tool_execute":
                        try:
                            return await self._execute_tool_streaming(args, eq, state.get("_on_activity"))
                        except NeedsPermission:
                            raise
                        except Exception as e:
                            logger.warning("tool_execute streaming failed, falling back to sync: %s", e)
                            from app.tools.filesystem import tool_execute as _sync_execute
                            result = await asyncio.to_thread(_sync_execute, **args)
                            return str(result)
                    result = await asyncio.to_thread(t.fn, **args)
                    return str(result)
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
                        mgr.add_temp_approval(e.path)
                        if eq and name == "tool_execute":
                            try:
                                return await self._execute_tool_streaming(args, eq, state.get("_on_activity"))
                            except NeedsPermission:
                                pass
                            except Exception as e2:
                                logger.warning("tool_execute streaming failed on retry, falling back: %s", e2)
                        result = await asyncio.to_thread(t.fn, **args)
                        return str(result)
                    return _permission_denied_msg(e.operation, e.path, name)
                except Exception as e:
                    return f"Error executing {name}: {e}"
        return f"Tool '{name}' not found"

    async def _execute_tool_streaming(self, args: dict, event_queue: asyncio.Queue, on_activity: Callable[[str], None] | None = None) -> str:
        """流式执行shell命令，实时推送输出到事件队列。使用异步子进程避免PIPE死锁。"""
        command = args.get("command", "")
        timeout = min(args.get("timeout", 300), 600)
        if timeout < 1:
            timeout = 5
        work_dir = args.get("work_dir", ".")

        resolved_cwd = Path(work_dir)
        if not resolved_cwd.is_absolute():
            resolved_cwd = Path(WORKSPACE) / resolved_cwd
        resolved_cwd = resolved_cwd.resolve()

        from app.permission import get_manager as _get_perm_mgr, NeedsPermission as _NeedsPermission
        mgr = _get_perm_mgr()
        decision = mgr.check(str(resolved_cwd), "execute")
        if decision == "deny":
            return f"Error: access denied to directory '{work_dir}'"
        if decision == "ask":
            raise _NeedsPermission(str(resolved_cwd), "execute", "tool_execute", args)

        # Apply whitelist check (same as filesystem.tool_execute)
        from app.tools.filesystem import _check_command_allowed, _check_command_blacklist
        try:
            _check_command_allowed(command)
            _check_command_blacklist(command)
        except ValueError as e:
            return f"Error: {e}"

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        start_time = tmod.time()
        last_heartbeat = tmod.time()

        async def _read_stream(stream, storage: list[str], source: str):
            nonlocal last_heartbeat
            while True:
                raw_line = await stream.readline()
                if not raw_line:
                    break
                now = tmod.time()
                if now - last_heartbeat >= 5:
                    last_heartbeat = now
                    try:
                        event_queue.put_nowait({
                            "type": "tool_heartbeat",
                            "tool_name": "tool_execute",
                            "elapsed_seconds": int(now - start_time),
                            "command": command[:200],
                        })
                    except Exception:
                        pass
                    if on_activity:
                        try:
                            on_activity(f"tool_execute 运行中 ({int(now - start_time)}s)")
                        except Exception:
                            pass
                decoded = raw_line.decode("utf-8", errors="replace").rstrip()
                storage.append(decoded)
                try:
                    event_queue.put_nowait({
                        "type": "tool_output",
                        "tool_name": "tool_execute",
                        "source": source,
                        "line": decoded,
                        "elapsed_seconds": int(tmod.time() - start_time),
                    })
                except Exception:
                    pass

        try:
            # Use exec (no shell) to prevent shell injection
            cmd_args = shlex.split(command)
            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(resolved_cwd) if resolved_cwd.is_dir() else None,
            )
        except Exception as e:
            return f"Error starting command: {e}"

        stdout_task = asyncio.create_task(_read_stream(process.stdout, stdout_lines, "stdout"))
        stderr_task = asyncio.create_task(_read_stream(process.stderr, stderr_lines, "stderr"))

        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            timed_out = True

        await asyncio.wait_for(asyncio.gather(stdout_task, stderr_task, return_exceptions=True), timeout=5)

        if timed_out:
            return f"Error: command timed out after {timeout}s"

        parts = []
        if stdout_lines:
            parts.append("\n".join(stdout_lines))
        if stderr_lines:
            parts.append(f"[stderr]\n" + "\n".join(stderr_lines))
        output = "\n".join(parts)
        rc = process.returncode or 0
        header = f"Exit code: {rc}"
        if output:
            return f"{header}\n{output}"
        return header

    def _push_stream_event(self, state: AgentState, event: dict):
        """流式文本增量事件：只进事件队列，不进 steps（避免污染步骤列表）。"""
        eq = state.get("_event_queue")
        if eq:
            eq.put_nowait(event)

    def _assemble_response(self, model: str, response, start: float, state: AgentState | None, push_text: bool = False):
        """记录调用指标并累加 token 用量，可选把非流式全文转为 text_delta 推送。"""
        dur = (tmod.time() - start) * 1000
        usage = getattr(response, "usage", None)
        pt = getattr(usage, "prompt_tokens", 0) if usage else 0
        ct = getattr(usage, "completion_tokens", 0) if usage else 0
        record_model_call(model, prompt_tokens=pt, completion_tokens=ct, duration_ms=dur)
        # 累加本次 invoke 的 token 用量（invoke 前重置），供 assistant 消息结算落库
        if not getattr(self, "_usage_accum", None):
            self._usage_accum = dict(_ZERO_USAGE)
        self._usage_accum["input"] += int(pt or 0)
        self._usage_accum["output"] += int(ct or 0)
        logger.info(
            "LLM call | model=%s pt=%d ct=%d dur=%.0fms",
            model, pt, ct, dur,
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
                )
            except Exception as exc:
                dur = (tmod.time() - start) * 1000
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
        record_model_call(model, prompt_tokens=int(pt or 0), completion_tokens=int(ct or 0), duration_ms=dur)
        if not getattr(self, "_usage_accum", None):
            self._usage_accum = dict(_ZERO_USAGE)
        self._usage_accum["input"] += int(pt or 0)
        self._usage_accum["output"] += int(ct or 0)
        logger.info(
            "LLM call | model=%s pt=%d ct=%d dur=%.0fms",
            model, int(pt or 0), int(ct or 0), dur,
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

        if state["context"]:
            context_parts = [
                f"[Source {i+1}]: {c['content']}"
                for i, c in enumerate(state["context"])
            ]
            context_text = "\n\n".join(context_parts)
            full_system_prompt = (
                self._system_prompt_with_kb()
                + "\n\n"
                + f"Retrieved Context:\n{context_text}"
            )
        else:
            full_system_prompt = self.system_prompt

        tool_defs = self._build_tool_defs()

        messages = [
            {"role": "system", "content": full_system_prompt},
        ]
        if state.get("history"):
            messages.extend(state["history"])

        # Build user content: text only or multimodal if files attached
        user_files = state.get("files", [])
        if user_files:
            user_content: list[dict] = [{"type": "text", "text": state["question"]}]
            for f in user_files:
                if f.get("mime_type", "").startswith("image/"):
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{f['mime_type']};base64,{f['data']}"},
                    })
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": state["question"]})

        model = state.get("model") or self.model
        if "/" not in model:
            if self.api_base and "deepseek" in self.api_base:
                model = f"deepseek/{model}"
            elif self.api_base and "openai" in self.api_base:
                model = f"openai/{model}"

        messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=usable_context_tokens(), reserve_tokens=0))

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
        while (
            msg.tool_calls or finish_reason == "tool-calls"
        ) and rounds < max_tool_rounds:
            rounds += 1

            # Compaction: compress old messages when context grows large
            # （先回溯清理旧工具输出，再判断是否触发压缩）
            messages = prune_tool_outputs(
                messages,
                protect_tokens=settings.tool_output_protect_tokens,
                minimum_tokens=settings.tool_output_prune_minimum_tokens,
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
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError as e:
                    early_results[tc.id] = f"Error parsing arguments for '{tool_name}': {e}"
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

            messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=usable_context_tokens(), reserve_tokens=0))
            # MAX_STEPS 注入后不再允许继续调用工具（对齐 opencode max-steps.ts 的 disable-tools 语义）
            final_tool_defs = None if steps_prompt_injected else tool_defs
            response = await self._llm_call(model, messages, final_tool_defs, state=state)
            msg = response.choices[0].message
            finish_reason = _normalize_finish_reason(getattr(response.choices[0], "finish_reason", None))

        record_model_call(
            model, duration_ms=(tmod.time() - _gen_start) * 1000,
            tool_rounds=rounds, tool_calls=tool_calls_count,
        )

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
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
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
            messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=usable_context_tokens(), reserve_tokens=0))
            # 对齐 opencode max-steps 语义：达到上限后工具禁用，仅注入收尾总结提示（assistant 角色）
            messages.append({"role": "assistant", "content": MAX_STEPS_PROMPT})
            response = await self._llm_call(model, messages, None, state=state)
            msg = response.choices[0].message
            finish_reason = _normalize_finish_reason(getattr(response.choices[0], "finish_reason", None))
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

    def refresh_tools(self):
        """刷新工具列表和系统提示，用于热更新技能和插件。"""
        self.tools = []
        self.tools.extend(create_filesystem_tools())
        if self.skill_loader:
            self.tools.extend(create_skill_tools(self.skill_loader))
        if self.plugin_loader:
            self.tools.extend(create_plugin_tools(self.plugin_loader))
        self.rebuild_system_prompt()
        self.graph = self._build_graph()

    async def invoke(self, question: str, model: str | None = None, history: list[dict] | None = None, use_vector_db: bool = True, files: list[dict] | None = None, event_queue: asyncio.Queue | None = None, conversation_id: str = "", on_activity: Callable[[str], None] | None = None) -> dict:
        """执行完整的RAG流程，返回回答和相关源。

        参数:
            conversation_id: 对话ID，传入时会自动创建并跟踪 TaskState。
        """
        # 可选：集成 TaskState 跟踪（当 conversation_id 不为空时）
        task = None
        if conversation_id:
            from app.context.task_state import TaskState
            task = TaskState(conversation_id=conversation_id)
            task.save()

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
