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

            # [token 优化 v9] 本次请求的 LLM 用量汇总（分解 + 子 Agent + 汇总），
            # 随 response payload 落库，与单 Agent executor 口径对齐。
            # 注：bus 事件循环对每个 agent 串行处理消息，无并发写冲突。
            self._usage = {"input": 0, "output": 0}

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
                    subtasks = [{"agent": "rag", "question": question}]

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
                    target_agent = subtasks[0]["agent"] if subtasks else "rag"
                    logger.info(
                        "Supervisor routing to '%s' (thread=%s)",
                        target_agent, msg.thread_id,
                    )
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
                    _tk = reply.payload.get("tokens") or {}
                    self._usage["input"] += _tk.get("input", 0)
                    self._usage["output"] += _tk.get("output", 0)
                yield AgentMessage(
                    source=self._id,
                    target="user",  # 由 bus.send 路由回 original 的调用者
                    type="response",
                    action="chat",
                    payload={
                        **reply.payload,
                        "routed_to": target_agent,
                        "tokens": dict(getattr(self, "_usage", {"input": 0, "output": 0})),
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

__all__ = ['SupervisorAgentCore']
