"""RAGAgent 的 BaseAgent 适配器。

将现有的 RAGAgent 包装为 BaseAgent 接口，
使它可以注册到 AgentBus 中，通过消息与其他 Agent 协作。

支持的动作:
  - "chat":     完整 RAG 增强对话（检索 + 生成）
  - "retrieve": 仅检索知识库（返回 sources）
  - "generate": 仅生成回答（不使用知识库）
"""

import logging
from typing import AsyncIterator, Callable, Optional

from app.agent.base import BaseAgent, AgentMessage
from app.agent.graph import RAGAgent
from app.agent.stream_events import TaggedEventQueue, agent_meta, emit

logger = logging.getLogger(__name__)


class RAGAgentWrapper(BaseAgent):
    """将现有的 RAGAgent 包装为 BaseAgent。"""

    def __init__(self, inner: RAGAgent, agent_id: str = "rag",
                 heartbeat: Optional[Callable[[str, str], None]] = None):
        self._inner = inner
        self._id = agent_id
        self._heartbeat = heartbeat

    @property
    def agent_id(self) -> str:
        return self._id

    def _notify(self, progress: str) -> None:
        """把处理进度转发给总线心跳（用于超时宽限续期 + 已完成步骤回传）。"""
        if self._heartbeat:
            try:
                self._heartbeat(self._id, progress)
            except Exception:
                pass

    async def handle_message(self, msg: AgentMessage) -> AsyncIterator[AgentMessage]:
        if msg.type != "request":
            logger.warning("RAGAgentWrapper received non-request message: %s/%s", msg.type, msg.action)
            return

        action = msg.action
        payload = msg.payload

        logger.info("RAGAgentWrapper handling: action=%s thread=%s", action, msg.thread_id)

        try:
            if action == "chat":
                # 完整 RAG 对话（事件桥：把 graph 步骤事件实时转发给前端）
                event_queue = payload.get("_event_queue")
                name, avatar = agent_meta(self._id)
                emit(event_queue, {
                    "type": "agent_start",
                    "agent_id": self._id,
                    "agent_name": name,
                    "agent_avatar": avatar,
                })
                tagged = TaggedEventQueue(event_queue, self._id) if event_queue is not None else None
                result = await self._inner.invoke(
                    question=payload.get("question", ""),
                    model=payload.get("model"),
                    history=payload.get("history", []),
                    use_vector_db=payload.get("use_vector_db", True),
                    files=payload.get("files", []),
                    conversation_id=payload.get("conversation_id", ""),
                    on_activity=self._notify,
                    event_queue=tagged,
                )
                emit(event_queue, {
                    "type": "agent_done",
                    "agent_id": self._id,
                    "content": result.get("answer", ""),
                })
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
                    model=payload.get("model"),
                    history=payload.get("history", []),
                    use_vector_db=True,
                    files=payload.get("files", []),
                    conversation_id=payload.get("conversation_id", ""),
                    on_activity=self._notify,
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
                    model=payload.get("model"),
                    history=payload.get("history", []),
                    use_vector_db=False,
                    files=payload.get("files", []),
                    conversation_id=payload.get("conversation_id", ""),
                    on_activity=self._notify,
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
            emit(payload.get("_event_queue"), {
                "type": "agent_error",
                "agent_id": self._id,
                "error": str(e),
            })
            yield AgentMessage(
                source=self._id, target=msg.source,
                type="error", action=action,
                payload={"error": str(e)},
                thread_id=msg.thread_id,
            )
