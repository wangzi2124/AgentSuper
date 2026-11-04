"""Unit tests for the SSE streaming seam (text deltas + realtime queue drain)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.api.compat import _graph_events, _part_event_to_sse


def test_text_delta_part_maps_to_sse():
    seen: list[str] = []
    agents: dict = {}
    out = _part_event_to_sse(
        {"type": "text_delta", "delta": "你好"}, seen, agents
    )
    assert out == [{"type": "text_delta", "agent_id": "", "delta": "你好"}]
    assert seen == []


def test_empty_text_delta_is_dropped():
    assert _part_event_to_sse({"type": "text_delta", "delta": ""}, [], {}) == []


class _BlockingGraph:
    """Pushes a part event, then blocks so the graph node never completes."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.pushed = False

    def astream(self, *args, **kwargs):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.pushed:
            self.pushed = True
            from graph.events import push_event

            push_event({"type": "text_delta", "delta": "live"})
            await self.gate.wait()
        raise StopAsyncIteration


@pytest.mark.asyncio
async def test_part_events_drain_while_graph_node_is_still_running():
    graph = _BlockingGraph()
    ctx = SimpleNamespace(
        graph=graph,
        persist_compaction=lambda *a, **k: None,
        request_permission=lambda *a, **k: None,
    )
    sess = SimpleNamespace(id="s1")
    agen = _graph_events(ctx, sess, {}, [], asyncio.Event(), {})

    first = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
    assert first == {"type": "text_delta", "agent_id": "", "delta": "live"}

    graph.gate.set()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(agen.__anext__(), timeout=2.0)


@pytest.mark.asyncio
async def test_cancel_wakes_blocked_graph_stream_immediately():
    graph = _BlockingGraph()
    cancel = asyncio.Event()
    ctx = SimpleNamespace(
        graph=graph,
        persist_compaction=lambda *a, **k: None,
        request_permission=lambda *a, **k: None,
    )
    sess = SimpleNamespace(id="s1")
    agen = _graph_events(ctx, sess, {}, [], cancel, {})

    first = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
    assert first == {"type": "text_delta", "agent_id": "", "delta": "live"}

    blocked = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0.05)
    assert not blocked.done(), "graph node should still be blocked"
    cancel.set()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(blocked, timeout=2.0)
