"""L2 集成测试：并行扇出图（agent.md §6.2）。"""

import asyncio

import pytest

from graph.parallel import (
    WORKER_AGENTS,
    build_parallel_graph,
    make_dispatch_node,
    make_merge_node,
    split_by_agent,
)


def test_split_by_agent_buckets_and_skips_done():
    tasks = [
        {"id": "t1", "agent": "file"},
        {"id": "t2", "agent": "code"},
        {"id": "t3", "agent": "research"},
        {"id": "t4", "agent": "done", "status": "done"},
        {"id": "t5", "agent": "ghost"},
    ]
    buckets = split_by_agent(tasks)
    assert [t["id"] for t in buckets["file"]] == ["t1", "t5"]  # 未知 agent 归 file
    assert [t["id"] for t in buckets["code"]] == ["t2"]
    assert [t["id"] for t in buckets["research"]] == ["t3"]
    assert set(buckets) == set(WORKER_AGENTS)


def test_dispatch_falls_back_to_message():
    from langchain_core.messages import HumanMessage

    node = make_dispatch_node()
    payload = node({"messages": [HumanMessage(content="列目录")]})
    buckets = payload["dispatch_plan"]
    assert [t["objective"] for t in buckets["file"]] == ["列目录"]


def test_merge_marks_plan_done_and_builds_draft():
    node = make_merge_node()
    state = {
        "task_plan": [{"id": "t1", "agent": "file"}, {"id": "t2", "agent": "code"}],
        "sub_results": {"t1": "A", "t2": "B"},
        "dispatch_plan": {"file": [{"id": "t1"}], "code": [{"id": "t2"}]},
    }
    payload = node(state)
    assert all(t["status"] == "done" for t in payload["task_plan"])
    assert "A" in payload["draft_output"] and "B" in payload["draft_output"]


@pytest.mark.asyncio
async def test_build_parallel_graph_runs_all_workers():
    async def runner(agent, task, context=""):
        await asyncio.sleep(0.01)
        return {"text": f"{agent}:{task}"}

    graph = build_parallel_graph(runner=runner)
    state = {
        "task_plan": [
            {"id": "t1", "agent": "file", "objective": "写 a"},
            {"id": "t2", "agent": "code", "objective": "写 b"},
            {"id": "t3", "agent": "research", "objective": "查 c"},
        ]
    }
    out = await graph.ainvoke(state, config={"configurable": {"thread_id": "pg1"}})
    assert out["sub_results"]["t1"] == "file:写 a"
    assert out["sub_results"]["t2"] == "code:写 b"
    assert out["sub_results"]["t3"] == "research:查 c"
    assert all(t["status"] == "done" for t in out["task_plan"])
    assert "file:写 a" in out["draft_output"]


@pytest.mark.asyncio
async def test_parallel_graph_captures_worker_exception():
    async def runner(agent, task, context=""):
        if agent == "code":
            raise RuntimeError("boom")
        return {"text": f"{agent}:ok"}

    graph = build_parallel_graph(runner=runner)
    state = {"task_plan": [{"id": "t1", "agent": "code", "objective": "x"}]}
    out = await graph.ainvoke(state, config={"configurable": {"thread_id": "pg2"}})
    assert "失败：boom" in out["sub_results"]["t1"]
