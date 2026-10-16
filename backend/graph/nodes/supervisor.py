"""Supervisor 节点（对齐 agent.md §3.2）。

职责：推进迭代计数（防死循环硬闸的计数源）；实际路由在 `graph/routers.py`。
"""

from __future__ import annotations

from typing import Any, Callable


def make_supervisor_node() -> Callable[[dict[str, Any]], dict[str, Any]]:
    def supervisor_node(state: dict[str, Any]) -> dict[str, Any]:
        return {"iteration_count": state.get("iteration_count", 0) + 1}

    return supervisor_node
