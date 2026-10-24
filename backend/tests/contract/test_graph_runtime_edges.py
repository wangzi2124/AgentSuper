from __future__ import annotations

import pytest

from app.deps import initial_state
from execution.filesystem import VirtualFileSystem
from graph.main_graph import build_main_graph
from models.router import ChatResult


class _Fake:
    tier = "fake"

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return ChatResult(content="ok", finish_reason="stop")


async def _nodes(graph, question: str) -> list[str]:
    out: list[str] = []
    async for chunk in graph.astream(
        initial_state(question),
        config={"configurable": {"thread_id": f"edge-{question[:6]}"}},
        stream_mode="updates",
    ):
        out.extend((chunk or {}).keys())
    return out


@pytest.mark.asyncio
async def test_bf01_main_path_visits_expected_nodes(tmp_path):
    graph = build_main_graph(
        model=_Fake(),
        vfs=VirtualFileSystem(root=str(tmp_path)),
        enable_task_tool=False,
    )
    nodes = await _nodes(graph, "go")
    assert {
        "filter",
        "supervisor",
        "plan",
        "execute_subagent",
        "synthesize",
        "memory_store",
    }.issubset(set(nodes))


@pytest.mark.asyncio
async def test_bf01_followup_reuses_same_thread_without_crash(tmp_path):
    graph = build_main_graph(
        model=_Fake(),
        vfs=VirtualFileSystem(root=str(tmp_path)),
        enable_task_tool=False,
    )
    first = await _nodes(graph, "first")
    second = await _nodes(graph, "second")
    assert "synthesize" in first and "synthesize" in second
