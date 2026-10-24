from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from execution.filesystem import VirtualFileSystem
from graph.main_graph import build_main_graph
from models.router import ChatResult


class _Fake:
    tier = "fake"

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return ChatResult(content="ok", finish_reason="stop")


@pytest.mark.asyncio
async def test_checkpoint_survives_new_graph_instance(tmp_path):
    saver = MemorySaver()
    config = {"configurable": {"thread_id": "resume-test"}}
    graph1 = build_main_graph(
        model=_Fake(),
        vfs=VirtualFileSystem(root=str(tmp_path)),
        checkpointer=saver,
        enable_task_tool=False,
    )
    await graph1.aupdate_state(
        config, {"messages": [HumanMessage(content="before crash", id="u1")]}
    )

    # Simulate a restart: a new compiled graph using the same persistent saver.
    graph2 = build_main_graph(
        model=_Fake(),
        vfs=VirtualFileSystem(root=str(tmp_path)),
        checkpointer=saver,
        enable_task_tool=False,
    )
    snap = await graph2.aget_state(config)
    messages = snap.values.get("messages") or []
    assert messages and messages[0].content == "before crash"
