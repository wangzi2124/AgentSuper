"""Supervisor Agent — 多 Agent 系统的编排者。

简易版本的核心职责：
  1. 接收用户的 "chat" 请求
  2. 用 LLM 判断用户意图，决定路由到哪个子 Agent
  3. 通过 AgentBus 转发请求并等待回复
  4. 将子 Agent 的回答包装后返回给用户

后续可增强方向：
  - 任务分解（将复杂问题拆成多个子任务并行执行）
  - 多轮对话中的上下文管理
  - 结果合成（多个子 Agent 结果汇总）
"""

import asyncio
import logging
import uuid
from typing import AsyncIterator

from app.agent.base import BaseAgent, AgentMessage
from app.agent.bus import AgentBus
from app.config import settings

logger = logging.getLogger(__name__)


class SupervisorAgent(BaseAgent):
    """简易 Supervisor Agent —— 用 LLM 做路由，将请求转发给合适的子 Agent。"""

    def __init__(self, bus: AgentBus):
        self._bus = bus
        self._id = "supervisor"
        self._model = settings.llm_model
        self._api_key = settings.llm_api_key
        self._api_base = settings.llm_api_base

    @property
    def agent_id(self) -> str:
        return self._id

    async def handle_message(self, msg: AgentMessage) -> AsyncIterator[AgentMessage]:
        if msg.type != "request":
            return

        action = msg.action
        payload = msg.payload

        if action == "chat":
            # ---- 步骤 1: 路由判断 ----
            target_agent = await self._route(payload.get("question", ""))
            logger.info("Supervisor routed to '%s' (thread=%s)", target_agent, msg.thread_id)

            # ---- 步骤 2: 转发到目标 Agent 并等待回复 ----
            try:
                reply = await self._bus.send_and_wait(
                    AgentMessage(
                        source=self._id,
                        target=target_agent,
                        type="request",
                        action="chat",
                        payload=payload,
                        thread_id=msg.thread_id,
                    ),
                    timeout=60.0,
                )

                # ---- 步骤 3: 将结果返回给调用方 ----
                if reply.type == "response":
                    yield AgentMessage(
                        source=self._id,
                        target=msg.source,
                        type="response",
                        action="chat",
                        payload={
                            **reply.payload,
                            "routed_to": target_agent,
                        },
                        thread_id=msg.thread_id,
                    )
                else:
                    yield AgentMessage(
                        source=self._id,
                        target=msg.source,
                        type="error",
                        action="chat",
                        payload={"error": f"Sub-agent returned unexpected type: {reply.type}"},
                        thread_id=msg.thread_id,
                    )

            except asyncio.TimeoutError:
                yield AgentMessage(
                    source=self._id, target=msg.source,
                    type="error", action="chat",
                    payload={"error": f"Agent '{target_agent}' did not respond in time"},
                    thread_id=msg.thread_id,
                )
            except Exception as e:
                logger.exception("Supervisor error routing to %s", target_agent)
                yield AgentMessage(
                    source=self._id, target=msg.source,
                    type="error", action="chat",
                    payload={"error": str(e)},
                    thread_id=msg.thread_id,
                )

        else:
            yield AgentMessage(
                source=self._id, target=msg.source,
                type="error", action=action,
                payload={"error": f"Supervisor doesn't support action: {action}"},
                thread_id=msg.thread_id,
            )

    async def _route(self, question: str) -> str:
        """判断用户问题应该路由到哪个 Agent。

        目前系统只有 RAG Agent，所有请求都路由到 "rag"。
        关键词匹配提供了快速路径避免不必要的 LLM 调用。
        后续添加新 Agent 后可启用下方的 LLM 路由。
        """
        q = question.strip().lower()

        # ---- 关键词匹配（快速路径） ----
        kb_keywords = [
            "文档", "小说", "角色", "对话", "章节", "故事", "内容", "知识库",
            "人物", "情节", "书中", "记载", "来源",
        ]
        if any(kw in q for kw in kb_keywords):
            return "rag"

        # TODO: 当有多个子 Agent 时启用 LLM 路由
        # try:
        #     response = await litellm.acompletion(...)
        #     decision = response.choices[0].message.content.strip().lower()
        #     if "general" in decision and self._bus.get_agent("general"):
        #         return "general"
        # except Exception as e:
        #     logger.warning("LLM routing failed: %s", e)

        return "rag"
