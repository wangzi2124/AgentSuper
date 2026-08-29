"""拆分模块 `generate`（含 RAGAgentGenerate）。

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
from app.context.budget import llm_call_budget

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
from .tools import RAGAgentTools
# ── 跨子模块依赖（自动生成）──
from .constants import DOOM_LOOP_PROMPT
from .constants import MAX_STEPS_PROMPT
from .constants import _DEDUP_READONLY_TOOLS
from .constants import _normalize_finish_reason
from .state import AgentState
from .state import _ZERO_USAGE
from .state import _attachment_parts
from .state import _find_attachment
logger = logging.getLogger(__name__)
# ── 类分块（verbatim，继承链切片）──
class RAGAgentGenerate(RAGAgentTools):
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
            # [F8 后端] 附件处理：
            #   - image/* → 多模态 image_url 数据块（LLM 原生看图）
            #   - 其余（pdf/docx/xlsx/txt/md/csv/json 等）→ 经 LangChain document
            #     loaders 解析为文本上下文（attachment_loader），让模型能读到文档正文。
            image_names, text_ctx = _attachment_parts(user_files)
            for name in image_names:
                f = _find_attachment(user_files, name)
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{f['mime_type']};base64,{f['data']}"},
                })
            if text_ctx:
                user_content.append({"type": "text", "text": text_ctx})
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
        messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=llm_call_budget(), reserve_tokens=0, tool_defs=tool_defs))
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

            # [C5 · 方案 D 基础] 每轮把执行进度落盘为 STEP_STATE（会话工作目录存在时），
            # 供长任务接力/断点续跑恢复；上下文只装摘要+当前步，旧轮次不再携带。
            if state.get("_cwd") and rounds >= 1:
                from app.context.step_state import write_step_state
                done = [f"round {rounds}: {tc.function.name}" for tc in msg.tool_calls]
                write_step_state(
                    state["_cwd"], rounds,
                    {
                        "objective": state.get("question", ""),
                        "completed": done,
                        "active": ["等待下一轮工具调用或收尾总结"],
                        "blocked": [],
                        "next_move": ["继续剩余子任务；若接近上下文上限则先输出已完成部分"],
                        "files": [],
                    },
                )

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
            messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=llm_call_budget(), reserve_tokens=0, tool_defs=final_tool_defs))
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
            messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=llm_call_budget(), reserve_tokens=0))
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

__all__ = ['RAGAgentGenerate']
