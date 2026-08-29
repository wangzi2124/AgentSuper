# -*- coding: utf-8 -*-
"""AgentBus 全量用例：注册/发现、心跳与进度、点对点/广播/直投/丢弃、
send_and_wait（直投成功/超时/分级宽限延长）、run_agent 事件循环（处理/
异常投递结构化 error）、prune/cancel、start_all/stop_all。

运行：pytest tests/test_agent_bus.py
"""
import asyncio
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.agent.base import BaseAgent, AgentMessage
from app.agent.bus import AgentBus


class FakeAgent(BaseAgent):
    def __init__(self, aid, handler=None, fail=False):
        self._aid = aid
        self._handler = handler
        self._fail = fail

    @property
    def agent_id(self):
        return self._aid

    async def handle_message(self, msg):
        if self._fail:
            raise RuntimeError("handler crash")
        if self._handler is not None:
            async for r in self._handler(msg):
                yield r
            return
        yield AgentMessage(
            source=self._aid, target=msg.source, type="response", action=msg.action,
            payload={"ok": True}, thread_id=msg.thread_id,
        )


def _req(target="worker", thread="t1", **kw):
    base = dict(source="user", target=target, type="request", action="chat", thread_id=thread)
    base.update(kw)
    return AgentMessage(**base)


# ── 注册 / 发现 ────────────────────────────────────────────────────────────

def test_register_and_discover():
    bus = AgentBus()
    a = FakeAgent("alpha")
    bus.register(a)
    assert bus.get_agent("alpha") is a
    assert bus.list_agents() == ["alpha"]
    assert bus.get_agent("nope") is None


def test_register_overwrite_warns(caplog):
    bus = AgentBus()
    bus.register(FakeAgent("x"))
    with caplog.at_level("WARNING"):
        bus.register(FakeAgent("x"))
    assert "already registered" in caplog.text


# ── 心跳 / 进度 ────────────────────────────────────────────────────────────

def test_touch_progress_dedup_and_cap():
    bus = AgentBus()
    bus.touch("a", "step1")
    bus.touch("a", "step1")  # 重复 → 不追加
    bus.touch("a", "step2")
    assert bus.agent_progress("a") == ["step1", "step2"]
    assert bus.agent_progress("missing") == []
    for i in range(10):
        bus.touch("b", f"s{i}")
    assert len(bus.agent_progress("b")) <= 8


# ── send ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_direct_response_delivery():
    bus = AgentBus()
    fut = asyncio.get_running_loop().create_future()
    bus._pending["t1"] = fut
    msg = AgentMessage(source="a", target="b", type="response", action="chat", thread_id="t1")
    await bus.send(msg)
    assert fut.done()
    assert fut.result() is msg
    assert "t1" not in bus._pending


@pytest.mark.asyncio
async def test_send_direct_error_delivery():
    bus = AgentBus()
    fut = asyncio.get_running_loop().create_future()
    bus._pending["t1"] = fut
    msg = AgentMessage(source="a", target="b", type="error", action="chat", thread_id="t1", payload={"error": "x"})
    await bus.send(msg)
    assert fut.done()
    assert fut.result().type == "error"


@pytest.mark.asyncio
async def test_send_direct_when_already_done():
    bus = AgentBus()
    fut = asyncio.get_running_loop().create_future()
    fut.set_result("old")
    bus._pending["t1"] = fut
    await bus.send(AgentMessage(source="a", target="b", type="response", action="chat", thread_id="t1"))
    assert fut.result() == "old"  # done future 不再被覆盖


@pytest.mark.asyncio
async def test_send_broadcast():
    bus = AgentBus()
    for aid in ("a", "b", "c"):
        bus.register(FakeAgent(aid))
    await bus.send(AgentMessage(source="a", target="*", type="request", action="chat"))
    assert not bus._mailboxes["a"].empty() is False or bus._mailboxes["b"].qsize() == 1
    assert bus._mailboxes["b"].qsize() == 1
    assert bus._mailboxes["c"].qsize() == 1


@pytest.mark.asyncio
async def test_send_to_unknown_target(caplog):
    bus = AgentBus()
    with caplog.at_level("WARNING"):
        await bus.send(AgentMessage(source="a", target="ghost", type="request", action="chat"))
    assert "Unknown target agent" in caplog.text


# ── send_and_wait ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_and_wait_assert_request():
    bus = AgentBus()
    with pytest.raises(AssertionError):
        await bus.send_and_wait(AgentMessage(source="u", target="w", type="response", action="chat"))


@pytest.mark.asyncio
async def test_send_and_wait_timeout():
    bus = AgentBus()
    bus.register(FakeAgent("idle"))  # 不启动事件循环 → 永不回复
    with pytest.raises(asyncio.TimeoutError):
        await bus.send_and_wait(_req(target="idle"), timeout=0.1)


@pytest.mark.asyncio
async def test_send_and_wait_grace_extension(caplog):
    bus = AgentBus()
    bus.register(FakeAgent("idle"))
    bus.touch("idle")  # 活动心跳 → 首次超时前延长一次
    with caplog.at_level("WARNING"):
        with pytest.raises(asyncio.TimeoutError):
            await bus.send_and_wait(_req(target="idle"), timeout=0.1)
    assert "still active, extending wait" in caplog.text


@pytest.mark.asyncio
async def test_send_and_wait_custom_grace_params():
    bus = AgentBus()
    bus.register(FakeAgent("idle"))
    with pytest.raises(asyncio.TimeoutError):
        await bus.send_and_wait(_req(target="idle"), timeout=0.1, grace_extensions=0, grace_window=5)


@pytest.mark.asyncio
async def test_send_and_wait_roundtrip_via_loop():
    bus = AgentBus()
    bus.register(FakeAgent("worker"))
    loop_task = asyncio.create_task(bus.run_agent("worker"))
    resp = await bus.send_and_wait(_req(target="worker"), timeout=5)
    assert resp.type == "response"
    assert resp.payload == {"ok": True}
    loop_task.cancel()
    await loop_task  # run_agent 吞掉 CancelledError 正常返回


@pytest.mark.asyncio
async def test_send_and_wait_error_message_delivered():
    bus = AgentBus()
    bus.register(FakeAgent("crash", fail=True))
    loop_task = asyncio.create_task(bus.run_agent("crash"))
    resp = await bus.send_and_wait(_req(target="crash"), timeout=5)
    assert resp.type == "error"
    assert resp.payload["error_type"] == "sub_agent_error"
    loop_task.cancel()
    await loop_task


# ── prune / cancel ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prune_done_pending():
    bus = AgentBus()
    loop = asyncio.get_running_loop()
    done = loop.create_future()
    done.set_result("x")
    pending = loop.create_future()
    bus._pending["done"] = done
    bus._pending["pending"] = pending
    assert bus.prune_done_pending() == 1
    assert "done" not in bus._pending
    assert "pending" in bus._pending


@pytest.mark.asyncio
async def test_cancel_pending():
    bus = AgentBus()
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    bus._pending["t1"] = fut
    assert bus.cancel_pending("t1") is True
    assert fut.cancelled()
    assert bus.cancel_pending("t1") is False
    assert bus.cancel_pending("nope") is False


# ── run_agent / start_all / stop_all ───────────────────────────────────────

@pytest.mark.asyncio
async def test_run_agent_not_registered():
    bus = AgentBus()
    await bus.run_agent("missing")  # 不抛
    assert "missing" not in bus._running


@pytest.mark.asyncio
async def test_run_agent_already_running():
    bus = AgentBus()
    bus.register(FakeAgent("a"))
    t = asyncio.create_task(bus.run_agent("a"))
    await asyncio.sleep(0.05)
    await bus.run_agent("a")  # 已在运行 → 直接返回
    t.cancel()


@pytest.mark.asyncio
async def test_run_agent_handle_message_loop():
    bus = AgentBus()
    seen = []

    async def handler(msg):
        seen.append(msg)
        yield AgentMessage(source="a", target=msg.source, type="response", action=msg.action,
                           payload={"n": 1}, thread_id=msg.thread_id)

    bus.register(FakeAgent("a", handler=handler))
    t = asyncio.create_task(bus.run_agent("a"))
    resp = await bus.send_and_wait(_req(target="a"), timeout=5)
    assert resp.payload == {"n": 1}
    assert len(seen) == 1
    t.cancel()
    await t


@pytest.mark.asyncio
async def test_run_agent_retries_on_loop_crash(monkeypatch):
    """事件循环级崩溃（mailbox.get 抛错）触发外层重试；恢复后继续处理消息。"""
    import app.agent.bus as bus_mod

    async def fake_sleep(s):
        pass
    monkeypatch.setattr(bus_mod.asyncio, "sleep", fake_sleep)
    bus = AgentBus()
    bus.register(FakeAgent("a"))
    inner = bus._mailboxes["a"]

    class FlakyQ:
        def __init__(self, inner):
            self.inner = inner
            self.fails = 1

        async def get(self):
            if self.fails > 0:
                self.fails -= 1
                raise RuntimeError("transport error")
            return await self.inner.get()

        def put_nowait(self, *a, **k):
            return self.inner.put_nowait(*a, **k)

        async def put(self, *a, **k):
            return await self.inner.put(*a, **k)

    bus._mailboxes["a"] = FlakyQ(inner)
    t = asyncio.create_task(bus.run_agent("a", max_retries=2))
    resp = await bus.send_and_wait(_req(target="a"), timeout=5)
    assert resp.payload == {"ok": True}
    t.cancel()
    await t


@pytest.mark.asyncio
async def test_run_agent_permanently_stopped(monkeypatch, caplog):
    import app.agent.bus as bus_mod

    async def fake_sleep(s):
        pass
    monkeypatch.setattr(bus_mod.asyncio, "sleep", fake_sleep)
    bus = AgentBus()
    bus.register(FakeAgent("a"))

    class AlwaysFlaky:
        async def get(self):
            raise RuntimeError("transport error")

    bus._mailboxes["a"] = AlwaysFlaky()
    with caplog.at_level("ERROR"):
        await bus.run_agent("a", max_retries=1)
    assert "permanently stopped" in caplog.text
    assert "a" not in bus._running


@pytest.mark.asyncio
async def test_start_stop_all():
    bus = AgentBus()
    bus.register(FakeAgent("a"))
    bus.register(FakeAgent("b"))
    tasks = bus.start_all()
    assert len(tasks) == 2
    await asyncio.sleep(0.05)
    bus.stop_all()
    await asyncio.gather(*tasks, return_exceptions=True)
    for t in tasks:
        assert t.done()