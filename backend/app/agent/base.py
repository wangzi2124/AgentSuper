"""多 Agent 系统的基类和消息协议。

定义所有 Agent 必须遵守的接口，以及 Agent 间通信的消息格式。
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AgentMessage:
    """Agent 间通信的消息格式。

    Attributes:
        source: 发送方的 agent_id
        target: 接收方的 agent_id（"*" 表示广播给除自己外的所有 Agent）
        type: 消息类型 — "request" / "response" / "error"
        action: 动作类型 — "chat" / "retrieve" / "generate" / "route" 等
        payload: 消息体，自由格式字典
        thread_id: 追踪同一会话链，回复消息会沿用请求的 thread_id
        created_at: 消息创建时间戳（自动生成）
    """
    source: str
    target: str
    type: str  # "request" | "response" | "error"
    action: str
    payload: dict = field(default_factory=dict)
    thread_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class BaseAgent(ABC):
    """所有 Agent 的抽象基类。

    每个 Agent 有一个唯一 ID，并实现 handle_message 方法来处理收到的消息。
    handle_message 是一个异步生成器，可以产生零到多条回复消息。
    """

    @property
    @abstractmethod
    def agent_id(self) -> str:
        """Agent 的唯一标识符。"""
        ...

    @abstractmethod
    async def handle_message(self, msg: AgentMessage) -> AsyncIterator[AgentMessage]:
        """处理收到的消息，产生零到多条回复消息。

        Args:
            msg: 收到的消息

        Yields:
            回复消息，每条都会被总线路由到目标 Agent
        """
        ...
