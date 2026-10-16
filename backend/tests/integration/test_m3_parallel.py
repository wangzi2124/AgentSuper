"""L2 测试：并行扇出（agent.md §5.2）。"""

import asyncio

import pytest

from graph.nodes.parallel import make_parallel_node


@pytest.mark.asyncio
async def test_parallel_executes_all_pending():
    state = {
        "task_plan": [
            {"id": "t1", "agent": "file", "desc": "写 a", "status": "pending"},
            {"id": "t2", "agent": "code", "desc": "写 b", "status": "pending"},
        ]
    }

    async def runner(agent, desc, context=""):
        await asyncio.sleep(0.01)
        return {"agent": agent, "text": f"{agent}:{desc}", "rounds": 1}

    node = make_parallel_node(runner, max_concurrency=2)
    payload = await node(state)
    assert payload["sub_results"]["t1"] and payload["sub_results"]["t2"]
    assert payload["task_plan"][0]["status"] == "done"
    assert payload["task_plan"][1]["status"] == "done"
    assert "file:写 a" in payload["draft_output"] and "code:写 b" in payload["draft_output"]


@pytest.mark.asyncio
async def test_parallel_concurrency_limit():
    state = {
        "task_plan": [
            {"id": "t1", "agent": "file", "desc": "a"},
            {"id": "t2", "agent": "file", "desc": "b"},
            {"id": "t3", "agent": "file", "desc": "c"},
        ]
    }
    running = []
    max_seen = 0

    async def runner(agent, desc, context=""):
        nonlocal max_seen
        running.append(1)
        max_seen = max(max_seen, len(running))
        await asyncio.sleep(0.05)
        running.pop()
        return {"agent": agent, "text": desc}

    node = make_parallel_node(runner, max_concurrency=2)
    await node(state)
    assert max_seen <= 2


@pytest.mark.asyncio
async def test_parallel_captures_exceptions_as_text():
    state = {"task_plan": [{"id": "t1", "agent": "file", "desc": "x"}]}

    async def runner(agent, desc, context=""):
        raise RuntimeError("boom")

    node = make_parallel_node(runner)
    payload = await node(state)
    assert "(子任务 t1 失败：boom)" in payload["sub_results"]["t1"]
    assert payload["task_plan"][0]["status"] == "done"
