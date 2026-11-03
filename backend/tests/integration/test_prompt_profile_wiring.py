"""集成：execute 节点与子 Agent 按本轮实际模型装配 provider profile。"""

from langchain_core.messages import HumanMessage

from execution.tool_registry import ToolRegistry
from graph.nodes.execute import make_execute_node
from graph.subagent import SUBAGENTS, run_subagent
from models.router import ChatResult


class CaptureModel:
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self.tier = "fake"
        self.calls: list[list[dict]] = []

    def model_name(self) -> str:
        return self._model_name

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls.append([dict(m) for m in messages])
        return ChatResult(content="ok", finish_reason="stop")


def _state() -> dict:
    return {
        "messages": [HumanMessage(content="看看目录")],
        "task_plan": [{"id": "t1", "desc": "看看目录", "status": "pending"}],
    }


async def test_execute_uses_provider_profile():
    model = CaptureModel("openai/gpt-4o")
    node = make_execute_node(model, ToolRegistry())
    await node(_state())
    system = model.calls[0][0]["content"]
    assert "# 全局规则（最高优先级）" in system
    assert "# GPT 系列行为准则" in system
    assert "知识库智能系统" in system
    assert "OpenCode" not in system
    assert "apply_patch" not in system


async def test_execute_falls_back_to_default_profile():
    model = CaptureModel("deepseek/deepseek-v4-pro")
    node = make_execute_node(model, ToolRegistry())
    await node(_state())
    system = model.calls[0][0]["content"]
    assert "# 通用模型行为准则" in system


async def test_execute_explicit_prompt_is_not_layered():
    model = CaptureModel("openai/gpt-4o")
    node = make_execute_node(model, ToolRegistry(), system_prompt="SYS")
    await node(_state())
    system = model.calls[0][0]["content"]
    assert system.startswith("SYS")
    assert "# GPT 系列行为准则" not in system


async def test_subagent_uses_provider_profile():
    model = CaptureModel("anthropic/claude-sonnet")
    result = await run_subagent(
        SUBAGENTS["code"], "写个函数", registry=ToolRegistry(), model=model
    )
    assert result["agent"] == "code"
    system = model.calls[0][0]["content"]
    assert "# 全局规则（最高优先级）" in system
    assert "# Claude 系列行为准则" in system
    assert "代码专家" in system
