"""L1 单元测试：图内 part 事件（opencode 语义）——工具循环实时发 tool_start/tool_end。

背景：前端/后端的消息模型早就是 opencode 的 part 结构（`text`/`tool`/`step-start`/
`step-finish`，tool part 带 `state.input`/`state.output`），但**没有任何生产者**——
`run_tool_loop` 只把工具调用收进 `tool_trace`，前端因此永远拿不到工具名、参数与
结果（工具行只能是空壳）。本测试锁定「工具调用 → part 事件」这条链路。

覆盖：
- 装了事件队列 → 收到 tool_start（含 tool_name/tool_args）+ tool_end（含 tool_result/耗时）。
- 同一 step_id 的 start/end 成对（前端按 step_id 原位更新，不会出现两条）。
- 没有装队列（后台任务/单测）→ 不抛异常，循环照常返回答案。
"""

from __future__ import annotations

import asyncio

from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.events import push_event, reset_event_queue, set_event_queue
from graph.nodes.base import run_tool_loop
from models.router import ChatResult, ToolCall


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="read_file",
            description="读文件",
            fn=lambda path: f"内容:{path}",
        )
    )
    return reg


class _ToolThenAnswerModel:
    """第 1 轮发起工具调用，第 2 轮给出最终答案。"""

    def __init__(self) -> None:
        self.tier = "fake"
        self.calls = 0

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return ChatResult(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="read_file",
                        arguments='{"path": "a.txt"}',
                    )
                ],
                finish_reason="tool-calls",
            )
        return ChatResult(content="读完了", finish_reason="stop")


async def test_tool_call_emits_part_events():
    q: asyncio.Queue = asyncio.Queue()
    token = set_event_queue(q)
    try:
        out = await run_tool_loop(
            _ToolThenAnswerModel(),
            [{"role": "user", "content": "读 a.txt"}],
            _registry(),
            agent_id="build",
        )
    finally:
        reset_event_queue(token)

    assert out["text"] == "读完了"

    events: list[dict] = []
    while not q.empty():
        events.append(q.get_nowait())

    starts = [e for e in events if e["type"] == "tool_start"]
    ends = [e for e in events if e["type"] == "tool_end"]
    assert len(starts) == 1 and len(ends) == 1
    # start/end 必须是同一条 part（前端按 step_id 原位更新）
    assert starts[0]["step_id"] == ends[0]["step_id"]
    # 工具名 + 参数（opencode: part.tool / state.input）
    assert starts[0]["tool_name"] == "read_file"
    assert starts[0]["tool_args"] == {"path": "a.txt"}
    assert starts[0]["status"] == "running"
    assert starts[0]["agent_id"] == "build"
    # 结果 + 耗时（opencode: state.output / time）
    assert ends[0]["tool_result"] == "内容:a.txt"
    assert ends[0]["status"] == "completed"
    assert ends[0]["duration_ms"] is not None


async def test_unknown_tool_marks_step_failed():
    q: asyncio.Queue = asyncio.Queue()
    token = set_event_queue(q)
    try:
        await run_tool_loop(
            _ToolThenAnswerModel(),
            [{"role": "user", "content": "go"}],
            ToolRegistry(),  # 未注册 read_file → 未知工具
            agent_id="build",
        )
    finally:
        reset_event_queue(token)

    events: list[dict] = []
    while not q.empty():
        events.append(q.get_nowait())
    ends = [e for e in events if e["type"] == "tool_end"]
    assert ends and ends[0]["status"] == "failed"


async def test_no_queue_is_noop():
    """没装队列时 push_event 是 no-op，循环不受影响。"""
    push_event({"type": "tool_start", "step_id": "x", "name": "y"})
    out = await run_tool_loop(
        _ToolThenAnswerModel(),
        [{"role": "user", "content": "读 a.txt"}],
        _registry(),
    )
    assert out["text"] == "读完了"
