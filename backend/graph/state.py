"""图状态定义（对齐 agent.md §3.1）。

原则（§8.3）：只增不改（节点返回增量字典，由 reducer 合并）；可序列化；
有界增长；可重放。
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from .usage import add_ints, merge_dicts, merge_usage


class AgentState(TypedDict, total=False):
    """主图全局状态。"""

    messages: Annotated[list[BaseMessage], add_messages]
    task_plan: list[dict[str, Any]]
    plan_tier: str
    current_task_id: str
    fanout_count: Annotated[int, add_ints]
    budget_total: float
    sub_results: Annotated[dict[str, str], merge_dicts]
    context_summary: str
    # 摘要之后保留的近期原文起点（稳定消息 id，跨模型切换不变）
    context_tail_start_id: str | None
    context_bound: bool
    context_window: int
    compress_threshold: int
    keep_recent_tokens: int
    context_compressed: bool
    compress_count: int
    memory_context: str
    rag_context: str
    use_vector_db: bool
    rag_done: bool
    rag_relevant: bool
    rewrite_count: int
    rewritten_query: str
    filesystem_state: dict[str, Any]
    token_usage: Annotated[dict[str, Any], merge_usage]
    iteration_count: int
    final_output: str
    user_id: str
    draft_output: str
    tool_rounds: int
    trace: list[dict[str, Any]]
    # 模型切换相关（会话内换模型不丢上下文）
    model_binding: dict[str, Any]
    model_switch_log: list[dict[str, Any]]
    pending_model_switch: str | None


class SubAgentState(TypedDict, total=False):
    """子 Agent 独立状态（上下文隔离）。"""

    messages: Annotated[list[BaseMessage], add_messages]
    task_description: str
    tools_available: list[str]
    local_files: dict[str, Any]
    result: str
