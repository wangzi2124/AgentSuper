"""图状态定义（对齐 agent.md §3.1）。

原则（§8.3）：只增不改（节点返回增量字典，由 reducer 合并）；可序列化；
有界增长；可重放。
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from .usage import merge_usage


class AgentState(TypedDict, total=False):
    """主图全局状态。"""

    messages: Annotated[list[BaseMessage], add_messages]
    task_plan: list[dict[str, Any]]
    current_task_id: str
    sub_results: dict[str, str]
    context_summary: str
    memory_context: str
    rag_context: str
    filesystem_state: dict[str, Any]
    token_usage: Annotated[dict[str, Any], merge_usage]
    iteration_count: int
    final_output: str
    user_id: str
    draft_output: str
    tool_rounds: int
    trace: list[dict[str, Any]]


class SubAgentState(TypedDict, total=False):
    """子 Agent 独立状态（上下文隔离）。"""

    messages: Annotated[list[BaseMessage], add_messages]
    task_description: str
    tools_available: list[str]
    local_files: dict[str, Any]
    result: str
