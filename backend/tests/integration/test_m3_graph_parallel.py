"""L2 集成测试：M3 多 Agent（并行扇出 + task 工具委派）。"""

import pytest
from langchain_core.messages import HumanMessage

from execution.filesystem import VirtualFileSystem
from graph.main_graph import build_main_graph, run_agent
from models.router import ChatResult


class DummyPlanModel:
    def __init__(self):
        self.tier = "fake"

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        # 返回并行子任务：file + research
        content = (
            "[\n"
            '  {"id": "t1", "agent": "file", "objective": "列出当前目录"},\n'
            '  {"id": "t2", "agent": "research", "objective": "简要说明 RAG 的用途"}\n'
            "]"
        )
        return ChatResult(content=content, finish_reason="stop")


class SubagentResultModel:
    def __init__(self, text="完成"):
        self.tier = "fake"
        self._text = text

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return ChatResult(content=self._text, finish_reason="stop")


class DelegatingMainModel:
    def __init__(self, script):
        self.tier = "fake"
        self._script = list(script)
        self.calls = []

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls.append({"messages": messages, "tools": tools or []})
        if self._script:
            return self._script.pop(0)
        return ChatResult(content="done", finish_reason="stop")


def _tool_call(name, args):
    import json

    from models.router import ToolCall

    return ChatResult(
        content="",
        tool_calls=[ToolCall(id=f"c_{name}", name=name, arguments=json.dumps(args))],
        finish_reason="tool-calls",
    )


@pytest.mark.asyncio
async def test_parallel_plan_decomposes_and_merges(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "plan_parallel_enabled", True)
    monkeypatch.setattr(settings, "plan_max_fanout_calls", 2)
    monkeypatch.setattr(settings, "plan_fanout_min_budget", 0.0)

    def subagent_models(spec):
        return SubagentResultModel(f"{spec.name} 输出")

    graph = build_main_graph(
        vfs=VirtualFileSystem(root=str(tmp_path)),
        plan_model=DummyPlanModel(),
        subagent_models=subagent_models,
        enable_task_tool=True,
    )
    out = await run_agent(
        "分别处理并汇总这几个独立子任务",
        graph=graph,
        thread_id="m3p1",
        budget_total=10.0,
    )
    assert "sub_results" in out and "t1" in out["sub_results"] and "t2" in out["sub_results"]
    assert "file 输出" in out["final_output"]
    assert "research 输出" in out["final_output"]
    assert out["iteration_count"] == 1  # plan → parallel_worker(×N) → synthesize → memory_store(END)


@pytest.mark.asyncio
async def test_task_tool_delegates_end_to_end(tmp_path):
    def subagent_models(spec):
        return SubagentResultModel(f"{spec.name} 已处理")

    main = DelegatingMainModel(
        [
            _tool_call("task", {"agent": "code", "instruction": "生成代码片段"}),
            ChatResult(content="委派完成", finish_reason="stop"),
        ]
    )
    graph = build_main_graph(
        model=main,
        vfs=VirtualFileSystem(root=str(tmp_path)),
        enable_task_tool=True,
        subagent_models=subagent_models,
    )
    out = await run_agent("让代码专家处理", graph=graph, thread_id="m3d1")
    assert out["final_output"] == "委派完成"
    # 回退模式下首轮把工具定义注入提示词（task 必须可用）
    prompt = "\n".join(
        str(m.get("content", "")) for m in main.calls[0]["messages"]
    )
    assert "task" in prompt
