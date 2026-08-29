"""拆分模块 `core`（含 RAGAgent）。

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
from .generate import RAGAgentGenerate
# ── 跨子模块依赖（自动生成）──
from .state import AgentState
from .state import _ZERO_USAGE
from .state import _extract_cache_usage
logger = logging.getLogger(__name__)
# ── 类分块（verbatim，继承链切片）──
class RAGAgent(RAGAgentGenerate):
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

__all__ = ['RAGAgent']
