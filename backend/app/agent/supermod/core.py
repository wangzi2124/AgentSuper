"""拆分模块 `core`（含 SupervisorAgentCore）。

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
from .base import SupervisorAgentBase
logger = logging.getLogger(__name__)

_USAGE_KEYS = ("input", "output", "reasoning", "cache_read", "cache_write")


def _merge_usage(acc: dict, other: dict) -> None:
    """逐键求和 usage（五键口径），就地更新 acc（对齐 assistant 消息 data.tokens）。"""
    for k in _USAGE_KEYS:
        acc[k] = int(acc.get(k, 0)) + int(other.get(k, 0) or 0)


# ── 类分块（verbatim，继承链切片）──
class SupervisorAgentCore(SupervisorAgentBase):

    # ═══════════════════════════════════════════════════════════════
    #  Handle Message （入口）
    # ═══════════════════════════════════════════════════════════════

    async def handle_message(self, msg: AgentMessage) -> AsyncIterator[AgentMessage]:
        if msg.type != "request":
            return

        action = msg.action
        payload = msg.payload

        if action == "chat":
            question = payload.get("question", "")

            # 语音消息降级兜底：前端转写失败时 message 仍为 "[语音]"，
            # 若 payload 携带 voice.text（后端转写/历史回放），用其替换占位符
            if question == "[语音]":
                voice_text = (payload.get("voice") or {}).get("text", "")
                if voice_text:
                    question = voice_text
                    payload["question"] = question

            # [token 优化 v9] 本次请求的 LLM 用量汇总（分解 + 子 Agent + 汇总），
            # 随 response payload 落库，与单 Agent executor 口径对齐。
            # 注：bus 事件循环对每个 agent 串行处理消息，无并发写冲突。
            # [token 统计] 五键口径 + cost 一并汇总。
            self._usage = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
            self._cost = 0.0

            # [A2] Supervisor 自身心跳：整个处理（LLM 分解 / 等待子 Agent / 汇总）
            # 期间持续 touch，让上层（endpoint send_and_wait 的 grace 续期）能看见
            # supervisor 仍存活，避免其被误判超时；收尾时取消。
            beat = self._start_heartbeat()
            try:
                # ── 尝试任务分解 ──
                subtasks = await self._decompose(question)

                # 安全护栏：只路由到白名单 Agent，防止 LLM 返回 "supervisor" 造成自我递归超时
                subtasks = [st for st in subtasks if st.get("agent") in self.ROUTABLE_AGENTS]
                if not subtasks:
                    subtasks = [{"agent": "build", "question": question}]

                if len(subtasks) > 1:
                    logger.info(
                        "Supervisor decomposed into %d subtasks (thread=%s)",
                        len(subtasks), msg.thread_id,
                    )
                    # 并行执行分解后的子任务
                    result = await self._execute_parallel(subtasks, payload, msg.thread_id)
                    yield result
                else:
                    # 只有一个子任务 → 走简单路由
                    target_agent = subtasks[0]["agent"] if subtasks else "build"
                    logger.info(
                        "Supervisor routing to '%s' (thread=%s)",
                        target_agent, msg.thread_id,
                    )
                    if target_agent == "plan" and self._should_handoff_to_build(question):
                        # [opencode 对齐] plan→build 顺序交接：plan 产出计划文件后
                        # supervisor 直接把计划交给 build 执行（对应 build-switch 语义）
                        async for reply in self._route_plan_then_build(payload, msg.thread_id):
                            yield reply
                    else:
                        async for reply in self._route_to(target_agent, payload, msg.thread_id):
                            yield reply
            finally:
                if beat is not None:
                    beat.cancel()

        else:
            yield AgentMessage(
                source=self._id, target=msg.source,
                type="error", action=action,
                payload={"error": f"Supervisor doesn't support action: {action}"},
                thread_id=msg.thread_id,
            )

    # ═══════════════════════════════════════════════════════════════
    #  Bug 修复: 使用独立 thread_id 发送子请求
  # ═══════════════════════════════════════════════════════════════

    async def _route_to(
        self,
        target_agent: str,
        payload: dict,
        original_thread_id: str,
    ) -> AsyncIterator[AgentMessage]:
        """转发到目标 Agent 并等待回复。

        🔧 Bug 修复: 子请求使用独立 thread_id，避免覆盖调用方的 Future。
        """
        sub_thread_id = f"{original_thread_id}:sub:{uuid.uuid4().hex[:8]}"
        timeout = self._timeout_for(target_agent)

        try:
            reply = await self._bus.send_and_wait(
                AgentMessage(
                    source=self._id,
                    target=target_agent,
                    type="request",
                    action="chat",
                    payload=payload,
                    thread_id=sub_thread_id,  # 🔧 独立 thread_id
                ),
                timeout=timeout,
            )

            if reply.type == "response":
                # [token 优化 v9] 子 Agent 用量计入本次请求汇总
                if getattr(self, "_usage", None) is not None:
                    _merge_usage(self._usage, reply.payload.get("tokens") or {})
                    self._cost = getattr(self, "_cost", 0.0) + float(reply.payload.get("cost", 0) or 0)
                yield AgentMessage(
                    source=self._id,
                    target="user",  # 由 bus.send 路由回 original 的调用者
                    type="response",
                    action="chat",
                    payload={
                        **reply.payload,
                        "routed_to": target_agent,
                        "tokens": dict(getattr(self, "_usage", {"input": 0, "output": 0})),
                        "cost": round(getattr(self, "_cost", 0.0), 6),
                    },
                    thread_id=original_thread_id,  # 🔧 使用原始 thread_id 回复
                )
            elif reply.type == "error":
                # bus 现在以 AgentMessage(type="error") 交付子 Agent 错误，
                # 透传 error payload（含 completed_steps 等上下文）。
                yield AgentMessage(
                    source=self._id, target="user",
                    type="error", action="chat",
                    payload={
                        "error": reply.payload.get("error", "Sub-agent failed"),
                        "error_type": reply.payload.get("error_type", "sub_agent_error"),
                        "completed_steps": reply.payload.get("completed_steps", []),
                    },
                    thread_id=original_thread_id,
                )
            else:
                yield AgentMessage(
                    source=self._id, target="user",
                    type="error", action="chat",
                    payload={"error": f"Sub-agent returned unexpected type: {reply.type}"},
                    thread_id=original_thread_id,
                )

        except asyncio.TimeoutError:
            logger.warning("Sub-agent '%s' timed out after %.0fs (thread=%s)", target_agent, timeout, original_thread_id)
            completed = self._bus.agent_progress(target_agent)
            suggestion = (
                f"如果任务仍在执行（如代码脚手架/构建），可提高 SUB_AGENT_TIMEOUT "
                f"或 SUB_AGENT_TIMEOUT_EXTENDED，或改用普通对话模式重试。"
            )
            yield AgentMessage(
                source=self._id, target="user",
                type="error", action="chat",
                payload={
                    "error": (
                        f"Agent '{target_agent}' did not respond in time (waited {timeout:.0f}s). "
                        f"已完成步骤: {(' → '.join(completed) if completed else '无可获取的处理进度')}. "
                        f"{suggestion}"
                    ),
                    "error_type": "sub_agent_timeout",
                    "timeout": timeout,
                    "completed_steps": completed,
                    "suggestion": suggestion,
                },
                thread_id=original_thread_id,
            )
        except Exception as e:
            logger.exception("Supervisor error routing to %s", target_agent)
            yield AgentMessage(
                source=self._id, target="user",
                type="error", action="chat",
                payload={
                    "error": str(e),
                    "error_type": "sub_agent_error",
                },
                thread_id=original_thread_id,
            )

    async def _collect_route(
        self,
        target_agent: str,
        payload: dict,
        original_thread_id: str,
    ) -> Optional[AgentMessage]:
        """路由到目标 Agent 并返回唯一回复消息（供顺序交接复用）。

        `_route_to` 恰好 yield 一条消息（response/error），收集后返回；
        无产出时返回 None。
        """
        reply = None
        async for m in self._route_to(target_agent, payload, original_thread_id):
            reply = m
        return reply

    async def _route_plan_then_build(
        self,
        payload: dict,
        original_thread_id: str,
    ) -> AsyncIterator[AgentMessage]:
        """[opencode 对齐] plan→build 顺序交接（build-switch 语义的无审批版）。

        当请求同时命中「规划」与「执行」意图时：
          1. 先路由 plan：产出实施计划并落盘 <data>/plans/<conv>/plan.md；
          2. 把计划文本（+ 计划文件路径）合成为 build 的 user 消息，再路由 build 执行；
          3. 合并为单条回复（计划 + 执行结果），保持与顶层 send_and_wait 的单回复契约。
        """
        plan_reply = await self._collect_route("plan", payload, original_thread_id)
        if plan_reply is None:
            yield AgentMessage(
                source=self._id, target="user",
                type="error", action="chat",
                payload={"error": "plan agent did not reply", "error_type": "sub_agent_error"},
                thread_id=original_thread_id,
            )
            return
        if plan_reply.type == "error":
            yield plan_reply
            return

        plan_answer = str(plan_reply.payload.get("answer", ""))
        plan_path = plan_reply.payload.get("plan_path", "")
        build_question = (
            "用户请求先规划再执行。plan agent 已产出以下实施计划"
            + (f"（计划文件: {plan_path}）" if plan_path else "")
            + "，请按计划逐步执行并报告完成情况：\n\n"
            + plan_answer
        )
        build_payload = dict(payload)
        build_payload["question"] = build_question
        build_reply = await self._collect_route("build", build_payload, original_thread_id)
        if build_reply is None:
            build_reply = AgentMessage(
                source=self._id, target="user",
                type="error", action="chat",
                payload={"error": "build agent did not reply", "error_type": "sub_agent_error"},
                thread_id=original_thread_id,
            )
        yield self._merge_plan_build_reply(plan_reply, build_reply)

    @staticmethod
    def _merge_plan_build_reply(
        plan_reply: AgentMessage,
        build_reply: AgentMessage,
    ) -> AgentMessage:
        """把 plan 与 build 两条子 Agent 回复合并为单条回复。

        build 成功时返回 response，answer = 计划 + ## 执行结果；
        build 失败时仍保留计划文本，返回 error 型消息且 answer 携带完整计划，便于用户跟进。
        plan_path/sources/steps/tokens 一并合并。
        """
        plan_payload = plan_reply.payload or {}
        build_payload = build_reply.payload or {}
        plan_answer = str(plan_payload.get("answer", ""))
        plan_path = plan_payload.get("plan_path", "")
        sources = list(plan_payload.get("sources") or []) + list(build_payload.get("sources") or [])
        steps = list(plan_payload.get("steps") or []) + list(build_payload.get("steps") or [])
        tokens = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
        _merge_usage(tokens, plan_payload.get("tokens") or {})
        _merge_usage(tokens, build_payload.get("tokens") or {})
        cost = round(float(plan_payload.get("cost", 0) or 0) + float(build_payload.get("cost", 0) or 0), 6)

        if build_reply.type == "error":
            answer = (
                f"## 实施计划\n\n{plan_answer}\n\n"
                f"## 执行结果（出错）\n{build_payload.get('error', 'build 执行出错')}"
            )
            return AgentMessage(
                source="supervisor", target=build_reply.target or "user",
                type="error", action="chat",
                payload={
                    "error": build_payload.get("error", "build 执行出错"),
                    "error_type": build_payload.get("error_type", "sub_agent_error"),
                    "completed_steps": build_payload.get("completed_steps", []),
                    "answer": answer,
                    "routed_to": "plan→build",
                    "plan_path": plan_path,
                    "tokens": tokens,
                    "cost": cost,
                    "sources": sources,
                    "steps": steps,
                },
                thread_id=build_reply.thread_id or plan_reply.thread_id,
            )

        answer = f"## 实施计划\n\n{plan_answer}\n\n## 执行结果\n{build_payload.get('answer', '')}"
        return AgentMessage(
            source="supervisor", target="user",
            type="response", action="chat",
            payload={
                "answer": answer,
                "sources": sources,
                "steps": steps,
                "tokens": tokens,
                "cost": cost,
                "plan_path": plan_path,
                "routed_to": f"plan→{build_payload.get('routed_to', 'build')}",
            },
            thread_id=build_reply.thread_id or plan_reply.thread_id,
        )

__all__ = ['SupervisorAgentCore']
