"""多 Agent 系统。

模块结构:
  - base.py:              BaseAgent 基类 + AgentMessage 消息协议
  - bus.py:               AgentBus 消息总线（注册、路由、事件循环）
  - memory.py:            共享记忆管理器（Agent 间上下文共享）
  - rag_wrapper.py:       RAGAgent 的 BaseAgent 适配器
  - web_search_agent.py:  网络搜索 Agent
  - code_agent.py:        代码辅助 Agent
  - supervisor.py:        Supervisor Agent（路由编排 + 任务分解）
  - graph.py:             单 Agent 的 LangGraph 状态机（被 rag_wrapper 包装）
"""

from app.agent.base import BaseAgent, AgentMessage
from app.agent.bus import AgentBus
from app.agent.memory import MemoryManager
from app.agent.rag_wrapper import RAGAgentWrapper
from app.agent.web_search_agent import WebSearchAgent
from app.agent.code_agent import CodeAgent
from app.agent.supervisor import SupervisorAgent

__all__ = [
    "BaseAgent",
    "AgentMessage",
    "AgentBus",
    "MemoryManager",
    "RAGAgentWrapper",
    "WebSearchAgent",
    "CodeAgent",
    "SupervisorAgent",
]
