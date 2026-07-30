"""多 Agent 系统。

模块结构:
  - base.py:          BaseAgent 基类 + AgentMessage 消息协议
  - bus.py:           AgentBus 消息总线（注册、路由、事件循环）
  - rag_wrapper.py:   RAGAgent 的 BaseAgent 适配器
  - supervisor.py:    Supervisor Agent（路由编排）
  - graph.py:         单 Agent 的 LangGraph 状态机（被 rag_wrapper 包装）
"""

from app.agent.base import BaseAgent, AgentMessage
from app.agent.bus import AgentBus
from app.agent.rag_wrapper import RAGAgentWrapper
from app.agent.supervisor import SupervisorAgent

__all__ = [
    "BaseAgent",
    "AgentMessage",
    "AgentBus",
    "RAGAgentWrapper",
    "SupervisorAgent",
]
