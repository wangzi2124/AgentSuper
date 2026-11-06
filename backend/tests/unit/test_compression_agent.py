from __future__ import annotations

from types import SimpleNamespace

import pytest

from harness.compression import summarize_messages
from harness.compression_agent import CompressionAgent


class _FakeModel:
    def __init__(self, text: str = "summary"):
        self.text = text
        self.calls: list[list[dict]] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=self.text)


@pytest.mark.asyncio
async def test_agent_sends_system_role_and_compression_task():
    model = _FakeModel("要点摘要")
    agent = CompressionAgent(model)

    out = await agent.summarize("旧摘要", "用户: 你好")

    assert out == "要点摘要"
    messages = model.calls[0]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "压缩 Agent" in messages[0]["content"]
    assert "<previous-summary>" in messages[1]["content"]
    assert "旧摘要" in messages[1]["content"]
    assert "用户: 你好" in messages[1]["content"]


def test_agent_prompt_falls_back_when_loader_fails(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("missing prompt")

    monkeypatch.setattr("prompts.loader.build_agent_prompt", _boom)
    agent = CompressionAgent(_FakeModel())

    prompt = agent.system_prompt()

    assert "compression agent" in prompt
    assert "Do not call tools" in prompt


@pytest.mark.asyncio
async def test_agent_resolves_simple_model_when_missing(monkeypatch):
    model = _FakeModel("auto")

    def _get_model(tier):
        assert tier == "simple"
        return model

    monkeypatch.setattr("models.router.get_model", _get_model)
    agent = CompressionAgent()

    out = await agent.summarize("", "用户: hi")

    assert out == "auto"
    assert agent.model is model


@pytest.mark.asyncio
async def test_summarize_messages_delegates_to_compression_agent(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_summarize(self, existing_summary, serialized):
        calls.append((existing_summary, serialized))
        return "agent summary"

    monkeypatch.setattr(CompressionAgent, "summarize", fake_summarize)
    from langchain_core.messages import HumanMessage

    out = await summarize_messages(
        [HumanMessage(content="hello", id="m1")],
        existing_summary="old",
        model=_FakeModel(),
    )

    assert out.startswith("agent summary")
    assert calls and calls[0][0] == "old"
