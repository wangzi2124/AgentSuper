"""L1 集成测试：SSE 传输层把图内 part 事件转成前端事件（opencode 语义）。

背景：`graph/nodes/base.py` 的工具循环会实时推 `tool_start`/`tool_end`，
`app/api/compat.py:_graph_events` 必须把它们转成前端认识的 `agent_start` /
`agent_step`（step 里带 tool_name/tool_args/tool_result），否则前端拿不到
工具名、参数与结果（工具行渲染成空壳）。

覆盖：
- 工具事件 → `agent_step`（含工具名/参数/结果），首次出现的 agent 先补 `agent_start`。
- 同一 step_id 的 start/end 在前端是「同一条 part 原位更新」。
- `agent_end` → `agent_done`（子 Agent 收尾）。
- agents 快照（历史回放用）里能拿到工具步骤。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.api.compat import _graph_events
from graph.events import push_event


class _FakeGraph:
    """最小主图：先推两个工具 part 事件，再完成一个节点。"""

    def astream(self, state, config=None, stream_mode=None):
        async def _agen():
            push_event(
                {
                    "type": "tool_start",
                    "step_id": "tool-c1",
                    "name": "read_file",
                    "status": "running",
                    "tool_name": "read_file",
                    "tool_args": {"path": "a.txt"},
                    "agent_id": "build",
                }
            )
            push_event(
                {
                    "type": "tool_end",
                    "step_id": "tool-c1",
                    "name": "read_file",
                    "status": "completed",
                    "duration_ms": 3.5,
                    "tool_name": "read_file",
                    "tool_result": "内容:a.txt",
                    "agent_id": "build",
                }
            )
            push_event(
                {
                    "type": "tool_start",
                    "step_id": "tool-c2",
                    "name": "write_file",
                    "status": "running",
                    "tool_name": "write_file",
                    "tool_args": {"path": "b.txt"},
                    "agent_id": "code",
                }
            )
            push_event({"type": "agent_end", "agent_id": "code", "agent_name": "code"})
            yield ("updates", {"execute_subagent": {"draft_output": "done"}})

        return _agen()


async def _collect():
    ctx = SimpleNamespace(graph=_FakeGraph())
    sess = SimpleNamespace(model=None, id="s1")
    seen: list[str] = []
    agents_map: dict = {}
    events = []
    async for ev in _graph_events(ctx, sess, {}, seen, asyncio.Event(), agents_map):
        events.append(ev)
    return events, agents_map, seen


def test_tool_events_become_agent_step_sse():
    events, agents_map, seen = asyncio.run(_collect())

    kinds = [e["type"] for e in events]
    assert "agent_start" in kinds
    assert "agent_step" in kinds
    assert "agent_done" in kinds

    steps = [e["step"] for e in events if e["type"] == "agent_step"]
    tool_steps = [s for s in steps if s.get("tool_name") == "read_file"]
    assert tool_steps, "工具 part 必须转成 agent_step"

    start = next(s for s in tool_steps if s["type"] == "tool_start")
    end = next(s for s in tool_steps if s["type"] == "tool_end")
    # 同一 step_id → 前端按它原位更新，不会出现两条 part
    assert start["step_id"] == end["step_id"]
    assert start["tool_args"] == {"path": "a.txt"}
    assert end["tool_result"] == "内容:a.txt"

    # agent_start 必须先于该 agent 的第一个 agent_step
    first_step_idx = next(i for i, e in enumerate(events) if e["type"] == "agent_step")
    start_idx = next(
        i
        for i, e in enumerate(events)
        if e["type"] == "agent_start" and e["agent_id"] == "build"
    )
    assert start_idx < first_step_idx

    # 历史回放用的 agents 快照里也有工具步骤
    build = agents_map["build"]
    assert any(s.get("tool_name") == "read_file" for s in build["steps"])
    # 子 Agent 收尾 → 面板状态 completed
    assert agents_map["code"]["status"] == "completed"
    assert "build" in seen and "code" in seen
