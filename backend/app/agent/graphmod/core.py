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

import json

import re

from collections.abc import Sequence

from pathlib import Path

from typing import Annotated, Callable, TypedDict

from app.context.token_counter import truncate_messages as _truncate_messages

from app.context.token_counter import sanitize_tool_messages

from app.context.token_counter import estimate_tokens_messages, estimate_tools, update_token_correction

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


def _reasoning_and_cost(model: str, usage, pt: int, ct: int, hit: int, miss: int) -> tuple[int, float]:
    """提取本次调用的 reasoning tokens 并按模型目录单价估算成本（USD）。

    [token 统计] 累加器放置处统一调用；价格未知的模型成本记 0（opencode 当前态）。
    """
    from app.models.catalog import resolve_cost
    rt = 0
    if usage is not None:
        det = getattr(usage, "completion_tokens_details", None)
        if det is not None:
            rt = int(getattr(det, "reasoning_tokens", 0) or 0)
        if not rt:
            rt = int(getattr(usage, "reasoning_tokens", 0) or 0)
    cost = resolve_cost(model, input_tokens=int(pt or 0), output_tokens=int(ct or 0),
                        cache_read=int(hit or 0), cache_write=int(miss or 0))
    return rt, cost

# [Ollama 流式兼容] litellm 的 Ollama 流式处理器不会把工具调用转成标准 tool_calls
# 增量，而是以 {"name": "<tool>", "arguments": {...}} 的 JSON 文本形式逐字流入
# delta.content。以下常量/函数用于识别并重组这类工具调用。
# 弱模型（qwen2.5-coder 等）常记不精确键名，放宽别名：name 键兼容 function/tool/
# action，arguments 键兼容 parameters/args/params；并支持 <tool_call> 显式标记。
_TCC_NAME_KEYS = ("name", "function", "tool", "tool_name", "action")
_TCC_ARGS_KEYS = ("arguments", "parameters", "args", "params", "input")
_TCC_MARKER_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_TCC_PATTERN = re.compile(
    r'^\s*\{\s*"name"\s*:\s*"([^"]*)"\s*,\s*"arguments"\s*:\s*(\{.*\})\s*\}\s*$', re.S
)
# 未知工具名时优先抽取的内嵌回复字段（qwen 常把答话放到 arguments.message/content）
_TCC_INNER_KEYS = ("message", "content", "text", "answer", "result", "response")


def _find_closing_quote(s: str):
    """s 以 \" 开头时返回闭合双引号后的下标；未闭合/不以引号开头返回 None。"""
    if not s or not s.startswith('"'):
        return None
    i = 1
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == '"':
            return i + 1
        i += 1
    return None


def _extract_tool_call_obj(content: str):
    """从内容中提取工具调用 JSON 对象（支持 <tool_call> 标记或整段裸 JSON）。

    兼容两种形状：
      A. 扁平 {name/function/tool..., arguments/parameters...}
      B. OpenAI tool_calls 消息 envelope {id, type, function: {name, arguments}}
    返回规范化的 {"name", "arguments"} 或 None。
    """
    if not content:
        return None
    m = _TCC_MARKER_RE.search(content)
    raw = (m.group(1) if m else content).strip()
    if len(raw) < 2 or not (raw.startswith("{") and raw.endswith("}")):
        return None
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    # B. OpenAI message envelope：function 是 {name, arguments} 对象而不是工具名
    fn = obj.get("function")
    if isinstance(fn, dict) and (fn.get("name") or fn.get("arguments")):
        return {
            "name": (fn.get("name") or "").strip(),
            "arguments": fn.get("arguments") if isinstance(fn.get("arguments"), dict) else {},
        }
    return obj


def _tcc_prefix_ok(text: str) -> bool:
    """宽松判断 text 是否仍是 Ollama 流式工具调用 JSON 的合法前缀。

    流式 token 切分无定式（"{\\"、\\"name\\"、\\"tools\\" 逐字节流出），逐个稳定键按
    状态机推进：{name键 : 值, arguments键 : {…。任一处偏离骨架 → 判定为普通文本。
    """
    t = text.strip()
    if not t:
        return True
    # <tool_call> 标记未收口 → 后续仍可能是工具调用
    if t.startswith("<tool_call>"):
        return "</tool_call>" not in t
    if not t.startswith("{"):
        return False
    rest = t[1:].lstrip()
    key = '"name"'
    for i in range(len(key)):
        if i >= len(rest):
            return True  # 键名还在流中
        if rest[i] != key[i]:
            return False
    rest = rest[len(key):].lstrip()
    if not rest:
        return True
    if not rest.startswith(":"):
        return False
    rest = rest[1:].lstrip()
    if not rest:
        return True
    if not rest.startswith('"'):
        return False
    end = _find_closing_quote(rest)
    if end is None:
        return True  # 值字符串未闭合
    rest = rest[end:].lstrip()
    if not rest:
        return True
    if not rest.startswith(","):
        return False
    rest = rest[1:].lstrip()
    ak = '"arguments"'
    for i in range(len(ak)):
        if i >= len(rest):
            return True
        if rest[i] != ak[i]:
            return False
    rest = rest[len(ak):].lstrip()
    if not rest:
        return True
    if not rest.startswith(":"):
        return False
    rest = rest[1:].lstrip()
    if not rest:
        return True
    if not rest.startswith("{"):
        return False
    # 已进入参数对象：外括号未收口时仍可能继续流；完整命中交给 _TCC_PATTERN
    return t.count("{") > t.count("}")


def _parse_streamed_tool_call(content: str):
    """识别 Ollama 流式注入 content 的工具调用 JSON 文本。

    兼容 {"name": "tool_x", "arguments": {...}} 标准形、别名键
    (function/tool + parameters/args) 以及 <tool_call>...</tool_call> 标记，
    返回 {"name", "arguments"} 或 None。
    """
    obj = _extract_tool_call_obj(content)
    if not obj:
        return None
    name = None
    for k in _TCC_NAME_KEYS:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            name = v.strip()
            break
    if not name:
        return None
    args = None
    for k in _TCC_ARGS_KEYS:
        v = obj.get(k)
        if isinstance(v, dict):
            args = v
            break
    if args is None:
        return None
    return {"name": name, "arguments": args}


def _sanitize_tool_call_content(content: str, mounted_names: set[str] | None = None):
    """把可能残留的工具调用 JSON 文本清洗为可展示内容。

    - 命中已挂载工具名的独立工具调用 JSON → 返回 None（这是应丢掉的工具声明，
      由调用方决定不展示）；未挂载工具名 → 抽取 arguments 内嵌回复字段。
    - 不匹配 → 原样返回。
    """
    if not content:
        return content
    call = _parse_streamed_tool_call(content)
    if not call:
        return content
    if mounted_names and call["name"] in mounted_names:
        return None
    for k in _TCC_INNER_KEYS:
        v = call["arguments"].get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


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
        # [C5] 用实际 usage 自适应校准估算系数（消除对 DeepSeek 中文/代码的系统性低估）
        if pt:
            pre = getattr(self, "_last_call_estimate", 0)
            if pre:
                update_token_correction(pre, int(pt))
        # 累加本次 invoke 的 token 用量（invoke 前重置），供 assistant 消息结算落库
        if not getattr(self, "_usage_accum", None):
            self._usage_accum = dict(_ZERO_USAGE)
        if not getattr(self, "_cost_accum", None):
            self._cost_accum = 0.0
        rt, cost = _reasoning_and_cost(model, usage, int(pt or 0), int(ct or 0), hit, miss)
        self._usage_accum["input"] += int(pt or 0)
        self._usage_accum["output"] += int(ct or 0)
        self._usage_accum["cache_read"] += hit
        self._usage_accum["cache_write"] += miss
        self._usage_accum["reasoning"] += rt
        self._cost_accum += cost
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
        # [C5] 记录本次调用的估算 token（供实际 usage 返回后自适应校准估算系数）
        self._last_call_estimate = estimate_tokens_messages(messages) + estimate_tools(tool_defs)
        # 参照 settings.llm_api_base/key 作为兜底；注册过 providers 的自定义模型
        # （前端模型管理写入 model_catalog.json）按 model 的 provider 解析 api_base/api_key。
        from app.models.catalog import provider_api
        _creds = provider_api(model)
        _is_ollama = _creds["is_ollama"]
        _api_key = _creds["api_key"]
        _api_base = _creds["api_base"]
        try:
            stream = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=tool_defs,
                api_key=_api_key,
                api_base=_api_base,
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
                    api_key=_api_key,
                    api_base=_api_base,
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
        pending_text: list[str] = []
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
                    pending_text.append(c)
                    # 仍可能属于 Ollama 流式工具调用 JSON 的文本 → 暂缓推送，
                    # 避免把工具调用 JSON 闪给前端；流结束统一转换/补推。
                    circum = "".join(text_chunks)
                    if _TCC_PATTERN.match(circum) or _tcc_prefix_ok(circum) or circum.lstrip().startswith("{"):
                        # 以 { 开头的待定文本在未收口（brace 未平衡）时仍可能是工具 JSON
                        if _TCC_PATTERN.match(circum) or _tcc_prefix_ok(circum):
                            continue
                        if circum.count("{") > circum.count("}"):
                            continue
                    pending_str = "".join(pending_text)
                    if pending_str.strip() in ("{}", "[]", ""):
                        # 纯空 JSON / 空白（弱模型占位或垃圾输出）不推给前端，
                        # 避免先闪现 {} 再被最终答案替换（前端 parts 会残留该段）。
                        pending_text.clear()
                        continue
                    if state is not None:
                        self._push_stream_event(state, {"type": "text_delta", "delta": pending_str})
                    pending_text.clear()
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

        # [Ollama 流式兼容] 工具调用被 litellm 注入为 content 中的 JSON 文本而非标准
        # tool_calls 增量（Ollama 原生把函数调用随 message 文本返回）。重组：
        #   1) 名称命中已挂载工具 → 重建为标准 tool_call，由 _generate 正常执行；
        #   2) 未知工具名 → 抽取模型内嵌回复字段（qwen 常把答话放进 arguments.message）。
        converted = False
        call = _parse_streamed_tool_call(content)
        if call and not tool_slots:
            mounted = {t.get("function", {}).get("name") for t in (tool_defs or [])}
            if call["name"] in mounted:
                tool_slots[0] = {
                    "id": f"call_{uuid.uuid4().hex[:16]}",
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                    },
                }
                content = ""
                converted = True
            else:
                for k in _TCC_INNER_KEYS:
                    v = call["arguments"].get(k)
                    if isinstance(v, str) and v.strip():
                        content = v.strip()
                        converted = True
                        break
        # 流式期间被暂缓的候选文本：最终未被识别为工具调用时补推，避免丢字。
        # 已转换为 tool_call / 已抽取内嵌回复的场合，其原文（JSON）不再外泄。
        if state is not None and pending_text and not converted:
            pending_str = "".join(pending_text)
            if pending_str.strip() not in ("{}", "[]"):
                self._push_stream_event(state, {"type": "text_delta", "delta": pending_str})

        dur = (tmod.time() - start) * 1000
        pt = getattr(usage, "prompt_tokens", 0) if usage else 0
        ct = getattr(usage, "completion_tokens", 0) if usage else 0
        hit, miss = _extract_cache_usage(usage, pt=int(pt or 0))
        # [token 精确化] provider 未返回 usage（本地/私服代理）→ 用 DeepSeek V4
        # 官方 tokenizer 原生计数补全，费用统计按此口径计费（不丢 token/成本数据）。
        if not (int(pt or 0) or int(ct or 0)):
            from app.context.token_counter import estimate_tokens_messages as _est_msgs
            from app.models.deepseek_tokenizer import count_tokens as _native_tok
            _ti = _est_msgs(messages)
            _to = _native_tok(content)
            if _ti and _to:
                pt, ct, hit, miss = _ti, _to, 0, 0
                trace("llm.native_usage", model=model, pt=pt, ct=ct)
        # [C5] 流式路径同样用实际 usage 自适应校准估算系数
        if int(pt or 0) > 0 and self._last_call_estimate:
            update_token_correction(self._last_call_estimate, int(pt))
        trace("llm.usage", where="invoke", model=model, pt=int(pt or 0), ct=int(ct or 0), cache_hit=hit, cache_miss=miss, duration_ms=dur)  # [token trace v7]
        if not getattr(self, "_usage_accum", None):
            self._usage_accum = dict(_ZERO_USAGE)
        if not getattr(self, "_cost_accum", None):
            self._cost_accum = 0.0
        rt, cost = _reasoning_and_cost(model, usage, int(pt or 0), int(ct or 0), hit, miss)
        record_model_call(model, prompt_tokens=int(pt or 0), completion_tokens=int(ct or 0), duration_ms=dur,
                            reasoning_tokens=rt, cache_read=hit, cache_write=miss, cost=cost)
        self._usage_accum["input"] += int(pt or 0)
        self._usage_accum["output"] += int(ct or 0)
        self._usage_accum["cache_read"] += hit
        self._usage_accum["cache_write"] += miss
        self._usage_accum["reasoning"] += rt
        self._cost_accum += cost
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
