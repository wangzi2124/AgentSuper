"""Slash command ``subtask: true`` runs isolated (no parent history)."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from app.deps import initial_state
from execution.filesystem import VirtualFileSystem
from execution.tool_registry import ToolRegistry
from graph.main_graph import build_main_graph
from graph.nodes import subtask as subtask_mod
from graph.nodes.subtask import make_subtask_node
from graph.routers import route_after_filter
from models.router import ChatResult


def test_route_after_filter_prefers_subtask():
    assert route_after_filter({"subtask": True}) == "subtask"
    assert route_after_filter({}) == "supervisor"


def test_initial_state_carries_subtask_flag():
    state = initial_state("do it", subtask=True, agent="build")
    assert state["subtask"] is True
    assert initial_state("do it")["subtask"] is False


class _Fake:
    tier = "fake"

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return ChatResult(content="ok", finish_reason="stop")


@pytest.mark.asyncio
async def test_subtask_node_isolates_history(monkeypatch):
    captured: dict = {}

    async def fake_loop(model, messages, registry, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return {"text": "answer", "rounds": 1, "usage": {}, "tool_history": []}

    monkeypatch.setattr(subtask_mod, "run_tool_loop", fake_loop)
    node = make_subtask_node(_Fake(), ToolRegistry())
    state = {
        "messages": [
            HumanMessage(content="old secret history"),
            HumanMessage(content="[命令：review] do the thing"),
        ],
        "subtask": True,
        "agent": "build",
    }
    out = await node(state)

    assert out["final_output"] == "answer"
    assert out["draft_output"] == "answer"
    assert out["sub_results"] == {"subtask": "answer"}

    sent = captured["messages"]
    assert sent[0]["role"] == "system"
    assert sent[-1] == {"role": "user", "content": "[命令：review] do the thing"}
    joined = "\n".join(str(m.get("content") or "") for m in sent)
    assert "old secret history" not in joined


@pytest.mark.asyncio
async def test_graph_routes_subtask_without_planning(tmp_path):
    graph = build_main_graph(
        model=_Fake(),
        vfs=VirtualFileSystem(root=str(tmp_path)),
        enable_task_tool=False,
    )
    nodes: list[str] = []
    state = initial_state("你好", subtask=True, agent="build")
    async for chunk in graph.astream(
        state,
        config={"configurable": {"thread_id": "subtask-thread"}},
        stream_mode="updates",
    ):
        nodes.extend((chunk or {}).keys())

    assert "subtask" in nodes
    assert "synthesize" in nodes
    assert "supervisor" not in nodes
    assert "plan" not in nodes
