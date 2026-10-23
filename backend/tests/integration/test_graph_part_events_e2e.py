"""L2 集成测试：真实主图里的工具调用 → part 事件（opencode 语义）端到端。

覆盖真实链路：`build_main_graph` → `execute` 节点 → `run_tool_loop` → 工具注册表
→ `graph.events` 队列。断言拿到的 part 事件带工具名、参数、结果，且归属于
正确的 agent 面板（主 Agent = build，task 委派的子 Agent = 各自名字）。
"""

import json

import pytest

from execution.filesystem import VirtualFileSystem
from graph.events import reset_event_queue, set_event_queue
from graph.main_graph import build_main_graph, run_agent
from models.router import ChatResult, ToolCall


class _ScriptedModel:
    """按脚本依次返回：先 tool_call，再最终答案。"""

    def __init__(self, script):
        self.tier = "fake"
        self._script = list(script)

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        if self._script:
            return self._script.pop(0)
        return ChatResult(content="完成", finish_reason="stop")


def _tool_call(name, args, call_id="c1"):
    return ChatResult(
        content="",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=json.dumps(args))],
        finish_reason="tool-calls",
    )


@pytest.mark.asyncio
async def test_main_graph_tool_call_emits_part_events(tmp_path):
    (tmp_path / "note.txt").write_text("你好", encoding="utf-8")

    model = _ScriptedModel(
        [
            _tool_call("tool_read_file", {"path": "note.txt"}),
            ChatResult(content="读到了内容", finish_reason="stop"),
        ]
    )
    graph = build_main_graph(
        model=model,
        vfs=VirtualFileSystem(root=str(tmp_path)),
    )

    q = __import__("asyncio").Queue()
    token = set_event_queue(q)
    try:
        out = await run_agent("读一下 note.txt", graph=graph, thread_id="part-e2e")
    finally:
        reset_event_queue(token)

    events = []
    while not q.empty():
        events.append(q.get_nowait())

    starts = [e for e in events if e["type"] == "tool_start"]
    ends = [e for e in events if e["type"] == "tool_end"]
    assert starts, "工具调用必须发出 tool_start"
    assert ends, "工具调用必须发出 tool_end"
    assert starts[0]["tool_name"] == "tool_read_file"
    assert starts[0]["agent_id"] == "build"
    assert starts[0]["step_id"] == ends[0]["step_id"]
    assert "note.txt" in json.dumps(starts[0]["tool_args"], ensure_ascii=False)
    assert ends[0]["status"] in {"completed", "failed"}
    assert ends[0]["tool_result"] is not None
    assert out["final_output"] == "读到了内容"


@pytest.mark.asyncio
async def test_subagent_tool_call_own_panel(tmp_path):
    """task 委派的子 Agent：工具 part 归属于子 Agent 自己的面板。"""
    (tmp_path / "note.txt").write_text("你好", encoding="utf-8")

    sub_model = _ScriptedModel(
        [
            _tool_call("tool_read_file", {"path": "note.txt"}, call_id="s1"),
            ChatResult(content="子任务完成", finish_reason="stop"),
        ]
    )

    main = _ScriptedModel(
        [
            _tool_call(
                "task",
                {"agent": "code", "instruction": "读一下 note.txt"},
                call_id="m1",
            ),
            ChatResult(content="委派完成", finish_reason="stop"),
        ]
    )
    graph = build_main_graph(
        model=main,
        vfs=VirtualFileSystem(root=str(tmp_path)),
        enable_task_tool=True,
        subagent_models=lambda spec: sub_model,
    )

    q = __import__("asyncio").Queue()
    token = set_event_queue(q)
    try:
        out = await run_agent("让代码专家读文件", graph=graph, thread_id="part-sub")
    finally:
        reset_event_queue(token)

    events = []
    while not q.empty():
        events.append(q.get_nowait())

    starts = [e for e in events if e["type"] == "tool_start"]
    assert starts, "子 Agent 的工具调用也要发 part 事件"
    # 主 Agent 的 task 委派本身也是一次工具调用（归属 build 面板）
    task_calls = [e for e in starts if e["tool_name"] == "task"]
    assert task_calls and task_calls[0]["agent_id"] == "build"
    # 子 Agent 自己的工具调用归属子面板
    sub_calls = [e for e in starts if e["tool_name"] == "tool_read_file"]
    assert sub_calls, "子 Agent 的工具调用必须发 part 事件"
    assert sub_calls[0]["agent_id"] == "code"
    ends = [e for e in events if e["type"] == "tool_end" and e["tool_name"] == "tool_read_file"]
    assert ends and ends[0]["step_id"] == sub_calls[0]["step_id"]
    # 子 Agent 收尾 → 面板关闭
    assert any(e["type"] == "agent_end" and e["agent_id"] == "code" for e in events)
    assert out["final_output"] == "委派完成"
