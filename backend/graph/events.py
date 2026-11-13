"""图执行事件（对齐 opencode 的 part 事件流）。

主图的工具循环在**执行过程中**把「工具调用」推入本回合的事件队列；API 层
（`app/api/compat.py`）把队列转成 SSE `agent_step` 事件下发，前端按 opencode 的
part 顺序渲染（`text` / `tool` / `step-start` / `step-finish`），并把工具参数、
结果挂到同一条 part 上（可展开查看）。

事件形状（字段与前端 `AgentStep` 对齐）::

    {
        "type": "tool_start" | "tool_end" | "step_start" | "step_end" | "agent_end",
        "step_id": "tool-call_9a45f0",
        "name": "read_file",
        "status": "running" | "completed" | "failed",
        "duration_ms": 12.3,
        "tool_name": "read_file",       # opencode: part.tool
        "tool_args": {...},             # opencode: state.input
        "tool_result": "...",           # opencode: state.output
        "agent_id": "build",            # 归属哪个 Agent 面板
    }

约定：
- 事件推送**永远不能影响主流程**（无队列 / 队列满 / 序列化失败一律静默）。
- 没有安装队列时（后台任务、单测）`push_event` 是 no-op —— 图照常跑。
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import Any

logger = logging.getLogger(__name__)

# 本回合的事件队列（asyncio.Queue）。None = 没有人在听（后台任务/单测）。
_event_queue: ContextVar[Any | None] = ContextVar("graph_event_queue", default=None)


def set_event_queue(queue: Any) -> Token:
    """安装本回合的事件队列，返回 reset token。"""
    return _event_queue.set(queue)


def reset_event_queue(token: Token) -> None:
    """卸载事件队列（必须与 set_event_queue 成对）。"""
    try:
        _event_queue.reset(token)
    except ValueError:  # pragma: no cover - token 来自别的 context
        _event_queue.set(None)


def push_event(event: dict[str, Any]) -> None:
    """把一条 part 事件推入本回合队列；没有队列时静默忽略。"""
    queue = _event_queue.get()
    if queue is None:
        return
    try:
        from harness.kernel import current_turn

        turn = current_turn()
        if turn is not None:
            event.setdefault("turn_id", turn.turn_id)
            event.setdefault("session_id", turn.session_id)
        queue.put_nowait(event)
    except Exception:  # noqa: BLE001 - 事件推送失败不得影响主流程
        logger.debug("push_event failed", exc_info=True)
