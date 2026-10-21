"""条件路由（对齐 agent.md §3.3）。"""

from __future__ import annotations

from typing import Any

from .nodes.base import content_text, has_image_content

MAX_ITERATIONS = 25
COMPRESS_THRESHOLD = 80_000
# 压缩节点（compress.py）在无 compressor 时仅当消息数 > KEEP_RECENT 才真正压缩；
# 路由须用同一门槛，否则触发压缩却不缩身 → supervisor↔compress 空转到 MAX_ITERATIONS。
KEEP_RECENT = 6


def _last_content(state: dict[str, Any]) -> str:
    messages = state.get("messages") or []
    if not messages:
        return ""
    return content_text(getattr(messages[-1], "content", ""))


def _has_image(state: dict[str, Any]) -> bool:
    """本回合用户消息是否带图片分块。"""
    for msg in reversed(state.get("messages") or []):
        if getattr(msg, "type", None) in ("human", "user"):
            return has_image_content(getattr(msg, "content", ""))
    return False


def _context_tokens(state: dict[str, Any]) -> int:
    """当前上下文的 token 估算（§4.3 压缩触发口径）。

    ⚠ 历史 bug：此处曾读 `token_usage["total_tokens"]`，但用量归一化
    （`graph/usage.py:usage_from_result`）产出的键是 `total` —— `total_tokens`
    全库无任何写入方，取值为 0，故 `0 > 80000` 恒假，压缩节点**从未被路由到**。
    压缩阈值应衡量「当前上下文长度」，直接对 `state.messages` 估算（与
    `cognition/compressor.py` 同口径），而非累计用量。
    """
    from cognition.compressor import estimate_tokens, serialize_messages

    messages = state.get("messages") or []
    if not messages:
        return 0
    return estimate_tokens(serialize_messages(messages))


def route_after_supervisor(state: dict[str, Any]) -> str:
    """Supervisor 根据当前状态决定下一步。"""

    if state.get("pending_model_switch"):
        return "switch_model"

    if state.get("iteration_count", 0) >= MAX_ITERATIONS:
        return "synthesize"

    if state.get("final_output"):
        return "end"

    if (
        not state.get("context_compressed")
        and _context_tokens(state) > COMPRESS_THRESHOLD
        and len(state.get("messages") or []) > KEEP_RECENT
    ):
        return "compress"

    plan = state.get("task_plan") or []
    pending = [t for t in plan if t.get("status") != "done"]

    if not plan:
        last = _last_content(state)
        # 用户显式勾选「知识库检索」(use_vector_db) → 本轮先检索一次知识库；
        # rag_done 门闩确保每轮只检索一次（避免 rag→execute→supervisor 空转）。
        # 优先级低于 [NEED_RAG] 哨兵（模型/子 Agent 主动要求检索）。
        if "[NEED_RAG]" in last:
            return "rag"
        if state.get("use_vector_db") and not state.get("rag_done"):
            return "rag"
        if "[NEED_MEMORY]" in last:
            return "recall"
        return "plan"

    if pending:
        return "execute"

    if state.get("sub_results") or state.get("draft_output"):
        return "synthesize"

    return "synthesize"


def should_plan_parallel(state: dict[str, Any]) -> bool:
    """并行扇出门禁（agent-1.md §2.4，成本受控核心）。

    三个条件**全部**满足才允许 Send 扇出（任缺一律退回 execute_subagent 串行）：
    - C3 任务特征：`plan_tier == "parallel"`（用户措辞含"分别/同时…"或长描述），
      且带 agent 归属的 pending 子任务 ≥ 2；
    - C2 次数：本会话 `fanout_count < plan_max_fanout_calls`（防连续扇出烧钱）；
    - C1 预算：本会话剩余预算 > `plan_fanout_min_budget`（未知预算视为 0 → 拒绝）。

    开关 `plan_parallel_enabled` **默认 False**（§四【不要做】：扇出默认关闭）。
    """
    if _has_image(state):
        return False
    if state.get("plan_tier") != "parallel":
        return False
    pending = [t for t in (state.get("task_plan") or []) if t.get("status") != "done"]
    agent_tasks = [t for t in pending if t.get("agent")]
    if len(agent_tasks) < 2:
        return False

    from config import settings

    if not getattr(settings, "plan_parallel_enabled", False):
        return False

    # C2：扇出次数上限。
    if int(state.get("fanout_count", 0) or 0) >= int(
        getattr(settings, "plan_max_fanout_calls", 2)
    ):
        return False

    # C1：剩余预算门禁（成本受控）——未知/为零预算不放行扇出。
    remaining = _remaining_budget(state)
    if remaining <= float(getattr(settings, "plan_fanout_min_budget", 0.20)):
        return False
    return True


def _remaining_budget(state: dict[str, Any]) -> float:
    """本会话剩余预算（§2.4 C1）。

    口径：`budget_total`（会话预算，若有）减去累计 `token_usage.cost`。
    未设预算时返回 0（保守：不放行高成本扇出）。
    """
    total = state.get("budget_total")
    if total is None:
        return 0.0
    try:
        return float(total) - float((state.get("token_usage") or {}).get("cost", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def route_after_plan(state: dict[str, Any]) -> Any:
    """计划完成后：并行门禁通过 → `Send` 动态扇出，否则单步执行。

    含图片的本回合**始终**走 `execute_subagent`（主 Agent 的视觉模型能看图）：
    子 Agent 上下文隔离、只拿结构化文本任务（§5.4），图片分块到不了它们手上，
    会让模型对着「看不到图」的指令硬猜（实测同一张蓝图时对时错）。

    并行由 `should_plan_parallel` 三门禁（预算/次数/任务特征）控制，默认关闭
    （`plan_parallel_enabled=False`）——高成本高收益，按需开启（§一 / §四）。
    """
    if _has_image(state):
        return "execute_subagent"
    if not should_plan_parallel(state):
        return "execute_subagent"

    from langgraph.types import Send

    pending = [t for t in (state.get("task_plan") or []) if t.get("status") != "done"]
    agent_tasks = [t for t in pending if t.get("agent")]
    sends = [
        Send(
            "parallel_worker",
            {
                "task_id": t.get("id", f"t{i + 1}"),
                "agent": t.get("agent"),
                "desc": t.get("desc") or t.get("objective") or "",
                "context": t.get("context") or "",
            },
        )
        for i, t in enumerate(agent_tasks)
    ]
    return sends or "execute_subagent"


# §13.4.2 grade 后路由：相关 → 生成；不相关且改写未达上限 → 改写重试；
# 改写达上限仍不相关 → 带空/低质结果放行（避免检索死循环）。
MAX_REWRITE = 2


def route_after_grade(state: dict[str, Any]) -> str:
    """§13.4.2：grade_docs 之后 → generate（原流程）或 rewrite（改写重试）。"""
    if state.get("rag_relevant"):
        return "generate"
    if int(state.get("rewrite_count", 0) or 0) >= MAX_REWRITE:
        return "generate"
    return "rewrite"
