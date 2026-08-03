"""AgentBus 任务 ↔ 子会话桥（设计文档 P4）。

AgentBus 的每次 `send_and_wait`（multi-agent 请求）在端点侧登记为一个
`kind='task'` 子会话（`parent_id` = 主会话），映射 `child_session_id ↔ thread_id`。

级联取消：interrupt / remove 父会话时，通过该映射取消对应 AgentBus 的
待处理 future（`AgentBus.cancel_pending`），使等待方感知取消。

映射为内存态（重启即失效，可接受；DB 侧子会话行本身持久化）。
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_bus: Optional[Any] = None
# child_session_id -> thread_id（AgentBus 消息 thread）
_threads: dict[str, str] = {}


def bind_bus(bus: Any) -> None:
    """应用启动时绑定 AgentBus 实例（main.py lifespan）。"""
    global _bus
    _bus = bus


def register(child_session_id: str, thread_id: str) -> None:
    """登记：子会话（kind='task'）正在等待 thread_id 对应的 AgentBus 回复。"""
    _threads[child_session_id] = thread_id


def unregister(child_session_id: str) -> None:
    _threads.pop(child_session_id, None)


def thread_for(child_session_id: str) -> Optional[str]:
    return _threads.get(child_session_id)


def cancel(child_session_id: str) -> bool:
    """取消单个子会话对应的 AgentBus 任务（有未完成 future 才算取消）。"""
    thread_id = _threads.pop(child_session_id, None)
    if thread_id is None or _bus is None:
        return False
    try:
        return _bus.cancel_pending(thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("task_bridge: cancel child %s failed: %s", child_session_id, exc)
        return False


def cancel_children(child_ids: list[str]) -> int:
    """级联取消一组子会话对应的 AgentBus 任务。"""
    cancelled = 0
    for cid in child_ids:
        if cancel(cid):
            cancelled += 1
    return cancelled
