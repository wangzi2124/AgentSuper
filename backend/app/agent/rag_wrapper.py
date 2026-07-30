"""RAGAgent 的 BaseAgent 适配器。

将现有的 RAGAgent 包装为 BaseAgent 接口，
使它可以注册到 AgentBus 中，通过消息与其他 Agent 协作。

支持的动作:
  - "chat":     完整 RAG 增强对话（检索 + 生成）
  - "retrieve": 仅检索知识库（返回 sources）
  - "generate": 仅生成回答（不使用知识库）
"""

import logging
from typing import AsyncIterator

from app.agent.base import BaseAgent, AgentMessage
from app.agent.graph import RAGAgent

logger = logging.getLogger(__name__)


class RAGAgentWrapper(BaseAgent):
    """将现有的 RAGAgent 包装为 BaseAgent。"""

    def __init__(self, inner: RAGAgent, agent_id: str = "rag"):
        self._inner = inner
        self._id = agent_id

    @property
    def agent_id(self) -> str:
        return self._id

    async def handle_message(self, msg: AgentMessage) -> AsyncIterator[AgentMessage]:
        if msg.type != "request":
            logger.warning("RAGAgentWrapper received non-request message: %s/%s", msg.type, msg.action)
            return

        action = msg.action
        payload = msg.payload

        logger.info("RAGAgentWrapper handling: action=%s thread=%s", action, msg.thread_id)

        try:
            if action == "chat":
                # 完整 RAG 对话
                result = await self._inner.invoke(
                    question=payload.get("question", ""),
                    model=payload.get("model"),
                    history=payload.get("history", []),
                    use_vector_db=payload.get("use_vector_db", True),
                    files=payload.get("files", []),
                    conversation_id=payload.get("conversation_id", ""),
                )
                yield AgentMessage(
                    source=self._id, target=msg.source,
                    type="response", action="chat",
                    payload={
                        "answer": result.get("answer", ""),
                        "sources": result.get("sources", []),
                        "steps": result.get("steps", []),
                    },
                    thread_id=msg.thread_id,
                )

            elif action == "retrieve":
                # 纯检索
                result = await self._inner.invoke(
                    question=payload.get("query", ""),
                    use_vector_db=True,
                )
                yield AgentMessage(
                    source=self._id, target=msg.source,
                    type="response", action="retrieve",
                    payload={
                        "answer": result.get("answer", ""),
                        "sources": result.get("sources", []),
                    },
                    thread_id=msg.thread_id,
                )

            elif action == "generate":
                # 纯生成（不检索）
                result = await self._inner.invoke(
                    question=payload.get("prompt", ""),
                    use_vector_db=False,
                )
                yield AgentMessage(
                    source=self._id, target=msg.source,
                    type="response", action="generate",
                    payload={"answer": result.get("answer", "")},
                    thread_id=msg.thread_id,
                )

            else:
                yield AgentMessage(
                    source=self._id, target=msg.source,
                    type="error", action=action,
                    payload={"error": f"Unknown action: {action}"},
                    thread_id=msg.thread_id,
                )

        except Exception as e:
            logger.exception("RAGAgentWrapper error on action=%s", action)
            yield AgentMessage(
                source=self._id, target=msg.source,
                type="error", action=action,
                payload={"error": str(e)},
                thread_id=msg.thread_id,
            )
