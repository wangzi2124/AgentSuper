# -*- coding: utf-8 -*-
"""F2 message.part.delta 真增量：text_delta 路由复现用例。

锁定 stream_events.py 的分派语义：
  - TaggedEventQueue 把 graph 的 text_delta 原样直通（打上 agent_id），而非像其它
    步骤事件那样改标为 agent_step、也不和其它未知类型一样被丢弃
  - AgentEventCollector 对 text_delta 只推 SSE 队列、不进入 events 快照
    （避免超长生成的增量文本撑爆快照；answer 终态由 done/落库兜底）
  - agent_step 仍按 [A4] 上限背压丢弃，text_delta 不受该上限阻塞
运行：pytest tests/test_stream_events.py
"""
import asyncio
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

from app.agent.stream_events import AgentEventCollector, TaggedEventQueue


def _drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_tagged_forwards_text_delta_with_agent_id():
    q: asyncio.Queue = asyncio.Queue()
    collector = AgentEventCollector(q)
    tagged = TaggedEventQueue(collector, "rag")

    tagged.put_nowait({"type": "text_delta", "delta": "你好"})
    tagged.put_nowait({"type": "text_delta", "delta": "，世界"})

    events = _drain(q)
    assert [e.get("type") for e in events] == ["text_delta", "text_delta"]
    assert events[0]["agent_id"] == "rag"
    assert events[0]["delta"] == "你好"
    assert events[1]["delta"] == "，世界"
    # 快照必须不含 text_delta（F2 语义：增量不进快照）
    assert collector.agents_snapshot() == []


def test_tags_unknown_and_step_still_mapped():
    q: asyncio.Queue = asyncio.Queue()
    collector = AgentEventCollector(q)
    tagged = TaggedEventQueue(collector, "code")

    tagged.put_nowait({"type": "tool_start", "step_id": "tool_ls", "name": "ls", "status": "running"})
    tagged.put_nowait({"type": "permission_request", "path": "/x", "operation": "read"})
    tagged.put_nowait({"type": "tool_output", "content": "noisy"})
    tagged.put_nowait({"type": "mystery_type"})

    events = _drain(q)
    types = {e.get("type") for e in events}
    # tool_start → agent_step；permission_request 透传；tool_output/未知类型丢弃
    assert "agent_step" in types
    assert "permission_request" in types
    assert "tool_output" not in types
    assert "mystery_type" not in types
    agent_step = next(e for e in events if e["type"] == "agent_step")
    assert agent_step["agent_id"] == "code"
    assert agent_step["step"]["step_id"] == "tool_ls"


def test_collector_text_delta_not_capped_by_overflow():
    q: asyncio.Queue = asyncio.Queue()
    collector = AgentEventCollector(q)
    collector._MAX_EVENTS = 3

    for i in range(10):
        collector.put_nowait({"type": "text_delta", "delta": str(i)})
    # 超过上限后 text_delta 仍应全部直通（不落入快照、不被丢弃）
    events = _drain(q)
    assert len(events) == 10
    assert collector.agents_snapshot() == []