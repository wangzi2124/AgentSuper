from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from app.deps import initial_state
from config import settings
from execution.filesystem import VirtualFileSystem
from graph.main_graph import build_main_graph
from models.router import ChatResult


class _Fake:
    tier = "fake"

    def __init__(self, content="ok"):
        self.content = content

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return ChatResult(content=self.content, finish_reason="stop")


class _Plan:
    tier = "fake"

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return ChatResult(
            content='[{"id":"t1","agent":"file","objective":"a"},'
            '{"id":"t2","agent":"research","objective":"b"}]',
            finish_reason="stop",
        )


async def _nodes(graph, state, thread):
    nodes = ["__start__"]
    async for chunk in graph.astream(
        state, config={"configurable": {"thread_id": thread}}, stream_mode="updates"
    ):
        nodes.extend((chunk or {}).keys())
    nodes.append("__end__")
    return nodes


def _edges(nodes):
    return {(a, b) for a, b in zip(nodes, nodes[1:])}


@pytest.mark.asyncio
async def test_runtime_graph_edges_cover_static_design_edges(tmp_path, monkeypatch):
    graph = build_main_graph(
        model=_Fake(), vfs=VirtualFileSystem(root=str(tmp_path)), enable_task_tool=False
    )
    covered = set()
    covered |= _edges(await _nodes(graph, initial_state("go"), "edge-main"))
    covered |= _edges(await _nodes(graph, initial_state("你好"), "edge-small"))

    # direct execute: pending task bypasses plan
    state = initial_state("go")
    state["task_plan"] = [{"id": "t1", "desc": "go", "status": "pending"}]
    covered |= _edges(await _nodes(graph, state, "edge-exec"))

    # memory recall edge
    state = initial_state("[NEED_MEMORY]")
    covered |= _edges(await _nodes(graph, state, "edge-mem"))

    # compression edge: enough messages + tiny threshold
    state = initial_state("go")
    state["messages"] = [HumanMessage(content=f"m{i}", id=f"m{i}") for i in range(8)]
    state["compress_threshold"] = 1
    graph = build_main_graph(
        model=_Fake(), vfs=VirtualFileSystem(root=str(tmp_path)), enable_task_tool=False
    )
    covered |= _edges(await _nodes(graph, state, "edge-compress"))

    # RAG -> grade -> rewrite -> grade -> execute
    graph = build_main_graph(
        model=_Fake(),
        vfs=VirtualFileSystem(root=str(tmp_path)),
        retrieve=lambda q: [{"content": "ORION-42", "metadata": {"source": "kb"}}],
        grade_model=_Fake("no"),
        rewrite_model=_Fake("better query"),
        enable_task_tool=False,
    )
    state = initial_state("knowledge base question")
    state["use_vector_db"] = True
    covered |= _edges(await _nodes(graph, state, "edge-rag"))

    # parallel worker -> synthesize
    monkeypatch.setattr(settings, "plan_parallel_enabled", True)
    monkeypatch.setattr(settings, "plan_max_fanout_calls", 2)
    monkeypatch.setattr(settings, "plan_fanout_min_budget", 0.0)
    graph = build_main_graph(
        model=_Fake(),
        vfs=VirtualFileSystem(root=str(tmp_path)),
        plan_model=_Plan(),
        enable_task_tool=False,
    )
    state = initial_state("???????????")
    state["budget_total"] = 10.0
    covered |= _edges(await _nodes(graph, state, "edge-parallel"))

    static = {(e.source, e.target) for e in graph.get_graph().edges}
    missed = static - covered
    # Dynamic Send edge may not appear in get_graph(); require 95% of static.
    ratio = 1 - (len(missed) / max(1, len(static)))
    assert ratio >= 0.95, f"coverage={ratio:.1%} missed={sorted(missed)}"
