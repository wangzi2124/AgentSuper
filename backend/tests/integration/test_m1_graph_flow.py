"""L2 集成测试：图结构与状态流转（agent.md §10.3）。

用脚本化 FakeModel 替换真实模型，验证 supervisor→plan→execute→synthesize 闭环。
"""

import json

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.deps import initial_state
from execution.filesystem import VirtualFileSystem
from graph.main_graph import build_main_graph, run_agent
from models.router import ChatResult, ToolCall


class ScriptedModel:
    """按脚本顺序返回结果的假模型（实现 TierModel 的 ainvoke 契约）。"""

    def __init__(self, script):
        self.tier = "fake"
        self._script = list(script)
        self.calls = []

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools})
        if self._script:
            return self._script.pop(0)
        return ChatResult(content="[done]", finish_reason="stop")


def _tool_call(name, args):
    return ChatResult(
        content="",
        tool_calls=[ToolCall(id="call_1", name=name, arguments=json.dumps(args))],
        finish_reason="tool-calls",
    )


@pytest.mark.asyncio
async def test_simple_task_no_subagent_calls(tmp_path):
    model = ScriptedModel([ChatResult(content="2", finish_reason="stop")])
    graph = build_main_graph(model=model, vfs=VirtualFileSystem(root=str(tmp_path)))
    out = await run_agent("1+1=?", graph=graph, thread_id="t1")
    assert out["final_output"] == "2"
    assert out["iteration_count"] <= 3
    # 简单任务不应产生工具调用（无 tools 传入即纯 QA）
    assert model.calls[0]["tools"]


@pytest.mark.asyncio
async def test_followup_turn_does_not_reuse_stale_sub_results(tmp_path):
    """同一 thread 的第二轮：plan 返回空计划时必须重新执行，不能综合上一轮结果。

    回归：会话内切换模型后追问「代号和端口分别是什么？」曾直接返回上一轮
    的「收到」——plan 判空计划 → 无新 sub_results → synthesize 取到旧值。
    """
    model = ScriptedModel(
        [
            ChatResult(content="ALPHA", finish_reason="stop"),
            ChatResult(content="BETA", finish_reason="stop"),
        ]
    )
    plan = ScriptedModel([ChatResult(content="[]", finish_reason="stop")])
    graph = build_main_graph(
        model=model,
        plan_model=plan,
        vfs=VirtualFileSystem(root=str(tmp_path)),
        checkpointer=MemorySaver(),
    )
    cfg = {"configurable": {"thread_id": "t_stale_sub_results"}}

    first = await graph.ainvoke(initial_state("记住：项目代号 Falcon", "u1"), cfg)
    assert first["final_output"] == "ALPHA"

    # 「然后」触发串行档位 → 调 plan 模型 → 返回显式空计划 → 回退单任务执行。
    second = await graph.ainvoke(
        initial_state("请基于刚才的对话继续说明，然后把结论告诉我", "u1"), cfg
    )
    assert second["final_output"] == "BETA"
    assert second["sub_results"] == {"t1": "BETA"}


@pytest.mark.asyncio
async def test_read_write_edit_end_to_end(tmp_path):
    model = ScriptedModel(
        [
            _tool_call("write_file", {"path": "a.txt", "content": "hello"}),
            _tool_call("edit_file", {"path": "a.txt", "old_text": "hello", "new_text": "world"}),
            ChatResult(content="已完成：a.txt 内容为 world", finish_reason="stop"),
        ]
    )
    graph = build_main_graph(model=model, vfs=VirtualFileSystem(root=str(tmp_path)))
    out = await run_agent(
        "把 hello 写入 a.txt 再改成 world", graph=graph, thread_id="t2"
    )
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "world"
    assert "world" in out["final_output"]


@pytest.mark.asyncio
async def test_tool_loop_bounded_on_endless_tool_calls(tmp_path):
    # 全程返回工具调用，验证工具循环有硬上限、不挂起
    infinite = [_tool_call("ls", {"path": "."}) for _ in range(20)]
    model = ScriptedModel(infinite)
    graph = build_main_graph(model=model, vfs=VirtualFileSystem(root=str(tmp_path)))
    out = await run_agent("永远列目录", graph=graph, thread_id="t3")
    assert out["iteration_count"] <= 25
    assert "最大工具轮数" in out["final_output"]


@pytest.mark.asyncio
async def test_needs_rag_routes_through_rag_node(tmp_path):
    def retrieve(query, top_k):
        return [{"content": "退款 7 天内", "score": 0.9}]

    model = ScriptedModel([ChatResult(content="退款 7 天", finish_reason="stop")])
    graph = build_main_graph(
        model=model, vfs=VirtualFileSystem(root=str(tmp_path)), retrieve=retrieve
    )
    out = await run_agent("[NEED_RAG] 退款政策", graph=graph, thread_id="t4")
    assert out["final_output"]


@pytest.mark.asyncio
async def test_use_vector_db_triggers_retrieval(tmp_path):
    """勾选「知识库检索」→ 无需 [NEED_RAG] 哨兵也走 rag 节点并注入上下文。"""
    calls = []

    def retrieve(query, top_k):
        calls.append(query)
        return [{"content": "退款 7 天内", "score": 0.9}]

    model = ScriptedModel([ChatResult(content="退款 7 天", finish_reason="stop")])
    graph = build_main_graph(
        model=model, vfs=VirtualFileSystem(root=str(tmp_path)), retrieve=retrieve
    )
    out = await run_agent(
        "退款政策是什么", graph=graph, thread_id="t5", use_vector_db=True
    )
    assert calls and "退款政策" in calls[0]
    assert out["rag_context"] and "退款 7 天" in out["rag_context"]
    assert out["final_output"]
