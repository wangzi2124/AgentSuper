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
    # 当前会话 id（prune 元数据持久化 / 观测使用）
    session_id: str
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
    # execute 节点报告的 system / tool schema token 开销（路由压缩阈值使用）
    context_system_tokens: int
    context_tool_tokens: int
    context_compressed: bool
    compress_count: int
    memory_context: str
    rag_context: str
    # 本轮注入的项目指令正文（来自 prompts/instructions.py；节点不读盘）。
    instructions: str
    # 本轮聊天非图片附件的解析正文（当前轮 human 被历史装配排除，由 execute 显式注入）
    attachment_context: str
    # 压缩检查点事件（auto/manual/overflow）；由 PlatformContext 同步到 session.db。
    compaction_event: dict[str, Any]
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
    # Bounded tool call/result history from the most recent tool-bearing turn.
    # Kept separate from ``messages`` so frontend history stays user/assistant only.
    tool_history: list[dict[str, Any]]
    tool_history_turn: int | None
    tool_history_anchor_id: str | None
    trace: list[dict[str, Any]]
    # 本轮使用的模型 id（初始状态写入；供 token 估算/路由读取）
    model: str
    # 模型绑定（实际切换由 `PlatformContext.switch_model` 完成，绑定存于
    # session_context_epoch.snapshot 与会话 model 字段；此处保留供 resolve_model 读）
    model_binding: dict[str, Any]


class SubAgentState(TypedDict, total=False):
    """子 Agent 独立状态（上下文隔离）。"""

    messages: Annotated[list[BaseMessage], add_messages]
    task_description: str
    tools_available: list[str]
    local_files: dict[str, Any]
    result: str
