"""L1/L2 单元测试：子 Agent 定义与执行（agent.md §5.4）。"""

import pytest

from execution.filesystem import VirtualFileSystem
from graph.main_graph import default_registry
from graph.subagent import SUBAGENTS, make_runner, make_task_tool, run_subagent
from models.router import ChatResult


class FakeModel:
    def __init__(self, content="子结果"):
        self.tier = "fake"
        self.content = content
        self.calls = []

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls.append({"messages": messages, "tools": tools or []})
        return ChatResult(content=self.content, finish_reason="stop")


def _registry(tmp_path):
    reg, _ = default_registry(VirtualFileSystem(root=str(tmp_path)), with_shell=False)
    return reg


def test_subagents_have_whitelists():
    assert set(SUBAGENTS) == {"research", "code", "file"}
    # research 只读：不含写类工具
    assert "write_file" not in SUBAGENTS["research"].tools
    assert "shell_exec" in SUBAGENTS["code"].tools


def test_default_registry_marks_writes_approval(tmp_path):
    reg = _registry(tmp_path)
    # 写类工具标记需审批（越界时由审批门控弹窗；沙箱内自动放行）
    assert reg.needs_approval("write_file") is True
    assert reg.needs_approval("edit_file") is True
    assert reg.needs_approval("append_file") is True
    assert reg.needs_approval("delete_file") is True
    # 读工具越界也要人工审批（沙箱内 hook 免审放行）
    for name in ("read_file", "ls", "grep", "glob"):
        assert reg.needs_approval(name) is True
    # 文件工具集已完整注册（glob/append/delete 在旧 vfs.tools() 中缺失）
    assert {"read_file", "write_file", "edit_file", "ls", "grep", "glob", "append_file", "delete_file"} <= set(
        reg.names()
    )


@pytest.mark.asyncio
async def test_run_subagent_restricts_tools(tmp_path):
    reg = _registry(tmp_path)
    # 注入一个全局工具，research 白名单不应包含它
    reg.register_function(lambda: "x", name="dangerous_tool", scope="global")
    model = FakeModel("研究结论")
    out = await run_subagent(SUBAGENTS["research"], "查一下", registry=reg, model=model)
    assert out["text"] == "研究结论"
    prompt = "\n".join(
        str(m.get("content", "")) for m in model.calls[0]["messages"]
    )
    assert "dangerous_tool" not in prompt
    assert "read_file" in prompt


@pytest.mark.asyncio
async def test_runner_unknown_agent_returns_error(tmp_path):
    runner = make_runner(_registry(tmp_path))
    out = await runner("ghost", "任务")
    assert "未知子 Agent" in out["text"]


@pytest.mark.asyncio
async def test_task_tool_delegates_and_returns_text(tmp_path):
    reg = _registry(tmp_path)
    runner = make_runner(reg, model_factory=lambda spec: FakeModel(f"{spec.name} 完成"))
    task = make_task_tool(runner)
    text = await task("code", "写一个函数")
    assert text == "code 完成"
