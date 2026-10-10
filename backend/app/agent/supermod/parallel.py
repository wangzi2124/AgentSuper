"""拆分模块 `parallel`（含 SupervisorAgent）。

原文件 docstring: Supervisor Agent — 多 Agent 系统的编排者。

核心职责:
  1. 接收用户的 "chat" 请求
  2. 用 LLM 判断用户意图，决定路由到哪个子 Agent
  3. 支持任务分解：将复杂问题拆成多个子任务并行执行
  4. 通过 AgentBus 转发请求并等待回复
  5. 将子 Agent 的回答包装后返回给用户

修复的 Bug:
  - thread_id 覆盖: 子请求使用独立 thread_id，防止覆盖调用方的 Future"""
# ── 复制自原模块的顶层 import ──
import asyncio

import logging

import re

import time as tmod

import uuid

from typing import AsyncIterator, Optional

import litellm

from app.agent.base import BaseAgent, AgentMessage

from app.agent.bus import AgentBus

from app.agent.memory import MemoryManager

from app.config import settings

from app.monitor import record_model_call

from app.utils.json_repair import parse_json_value
from .decompose import SupervisorAgentDecompose
# ── 跨子模块依赖（自动生成）──
from .constants import SUB_RESULT_TRUNC
from .constants import SYNTHESIS_SYSTEM_PROMPT
logger = logging.getLogger(__name__)
# ── 类分块（verbatim，继承链切片）──
class SupervisorAgent(SupervisorAgentDecompose):

    # ═══════════════════════════════════════════════════════════════
    #  并行执行
    # ═══════════════════════════════════════════════════════════════

    async def _execute_parallel(
        self,
        subtasks: list[dict],
        original_payload: dict,
        original_thread_id: str,
    ) -> AgentMessage:
        """并行执行多个子任务并汇总结果。"""
        results: list[dict] = []
        errors: list[str] = []

        async def run_one(st: dict) -> dict:
            """执行单个子任务。"""
            sub_thread_id = f"{original_thread_id}:decomp:{uuid.uuid4().hex[:8]}"
            timeout = self._timeout_for(st["agent"])
            try:
                # 构建子任务 payload（继承原始 payload，但 question 用子任务的）
                sub_payload = dict(original_payload)
                sub_payload["question"] = st.get("question", original_payload.get("question", ""))

                reply = await self._bus.send_and_wait(
                    AgentMessage(
                        source=self._id,
                        target=st["agent"],
                        type="request",
                        action="chat",
                        payload=sub_payload,
                        thread_id=sub_thread_id,
                    ),
                    timeout=timeout,
                )
                if reply.type == "error":
                    return {
                        "agent": st["agent"],
                        "original_question": st["question"],
                        "answer": "",
                        "error": reply.payload.get("error", "Sub-agent failed"),
                        "error_type": reply.payload.get("error_type", "sub_agent_error"),
                        "completed_steps": reply.payload.get("completed_steps", []),
                    }
                return {
                    "agent": st["agent"],
                    "original_question": st["question"],
                    "answer": reply.payload.get("answer", ""),
                    "sources": reply.payload.get("sources", []),
                    # [token 优化 v9] 透传子 Agent 用量，便于汇总
                    "tokens": reply.payload.get("tokens") or {},
                }
            except asyncio.TimeoutError:
                completed = self._bus.agent_progress(st["agent"])
                suggestion = (
                    f"如果任务仍在执行（如代码脚手架/构建），可提高 SUB_AGENT_TIMEOUT "
                    f"或 SUB_AGENT_TIMEOUT_EXTENDED，或改用普通对话模式重试。"
                )
                return {
                    "agent": st["agent"],
                    "original_question": st["question"],
                    "answer": "",
                    "error": (
                        f"Agent '{st['agent']}' did not respond in time (waited {timeout:.0f}s). "
                        f"已完成步骤: {(' → '.join(completed) if completed else '无可获取的处理进度')}."
                    ),
                    "error_type": "sub_agent_timeout",
                    "completed_steps": completed,
                    "suggestion": suggestion,
                }
            except Exception as e:
                return {
                    "agent": st["agent"],
                    "original_question": st["question"],
                    "answer": "",
                    "error": str(e),
                    "error_type": "sub_agent_error",
                }

        # 并行执行所有子任务
        task_results = await asyncio.gather(*[run_one(st) for st in subtasks])

        # [token 优化 v9] 各子 Agent 用量计入本次请求汇总
        if getattr(self, "_usage", None) is not None:
            for r in task_results:
                _tk = r.get("tokens") or {}
                self._usage["input"] += _tk.get("input", 0)
                self._usage["output"] += _tk.get("output", 0)

        for r in task_results:
            if r.get("error"):
                note = f"[{r['agent']}] {r['error']}"
                cs = r.get("completed_steps") or []
                if cs:
                    note += f" 已完成: {' → '.join(cs)}"
                errors.append(note)
            else:
                results.append(r)

        # 合成最终回答
        if len(results) == 1:
            # 只有一个成功的结果
            r = results[0]
            return AgentMessage(
                source=self._id, target="user",
                type="response", action="chat",
                payload={
                    "answer": r["answer"],
                    "sources": r.get("sources", []),
                    "steps": [],
                    "routed_to": r["agent"],
                    "tokens": dict(getattr(self, "_usage", {"input": 0, "output": 0})),
                },
                thread_id=original_thread_id,
            )
        else:
            # 多个结果 → 合成
            answer = await self._synthesize(question=original_payload.get("question", ""), results=results)
            all_sources = []
            for r in results:
                all_sources.extend(r.get("sources", []))

            error_note = ""
            if errors:
                error_note = f"\n\n⚠️ 部分 Agent 执行出错: {'; '.join(errors)}"

            return AgentMessage(
                source=self._id, target="user",
                type="response", action="chat",
                payload={
                    "answer": answer + error_note,
                    "sources": all_sources,
                    "steps": [],
                    "routed_to": "+".join(r["agent"] for r in results),
                    "tokens": dict(getattr(self, "_usage", {"input": 0, "output": 0})),
                },
                thread_id=original_thread_id,
            )
    async def _synthesize(
        self,
        question: str,
        results: list[dict],
    ) -> str:
        """将多个并行结果合成为一个回答。"""
        if not results:
            return "抱歉，所有 Agent 都未能返回结果。"

        segments = []
        for i, r in enumerate(results):
            agent_label = {"rag": "知识库", "web_search": "网络搜索", "code": "代码分析"}.get(r["agent"], r["agent"])
            # [token 优化 v4] 子 Agent 结果超长时截断，避免多结果汇总输入膨胀
            # （完整答案已由单 Agent 路由直接返回给用户；汇总仅需其要点）
            answer = r.get("answer", "")
            if len(answer) > SUB_RESULT_TRUNC:
                answer = answer[:SUB_RESULT_TRUNC] + f"\n…[子 Agent 结果过长，已截断前 {SUB_RESULT_TRUNC} 字符]"
            segments.append(
                f"[{agent_label} — {r.get('original_question', '')[:50]}]\n{answer}"
            )

        context = "\n\n---\n\n".join(segments)

        try:
            start = tmod.time()
            response = await litellm.acompletion(
                model=self._model,
                api_key=self._api_key,
                api_base=self._api_base,
                messages=[
                    {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                    {"role": "user", "content": f"用户问题: {question}\n\n各 Agent 返回:\n{context}"},
                ],
                max_tokens=2048,
                temperature=0.3,
            )
            dur = (tmod.time() - start) * 1000
            usage = getattr(response, "usage", None)
            pt = getattr(usage, "prompt_tokens", 0) if usage else 0
            ct = getattr(usage, "completion_tokens", 0) if usage else 0
            # [token 优化 v9] 汇总调用的用量计入本次请求
            if getattr(self, "_usage", None) is not None:
                self._usage["input"] += pt
                self._usage["output"] += ct
            record_model_call(self._model, prompt_tokens=pt, completion_tokens=ct, duration_ms=dur)
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error("Synthesis LLM call failed: %s", e)
            # 回退：拼接结果
            lines = ["以下是多个来源的信息汇总:\n"]
            for r in results:
                lines.append(f"--- {r['agent']} ---")
                lines.append(r.get("answer", "（无结果）"))
            return "\n".join(lines)

__all__ = ['SupervisorAgent']
