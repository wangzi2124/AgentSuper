import json
from types import SimpleNamespace

import pytest

from config import settings
from harness.delimiters import ARGS_CLOSE, ARGS_OPEN, NAME_CLOSE, NAME_OPEN
from models.router import ChatResult, TierModel
from models.stream import StreamResult

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "tool_echo",
            "description": "回显",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
        },
    }
]


def _response(content="", tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason="stop")],
        usage=None,
        model="fake-model",
    )


def _model(monkeypatch, mode, endpoint_model="fake-model"):
    monkeypatch.setattr(settings, "harness_tool_mode", mode)
    m = TierModel(tier="auto")
    monkeypatch.setattr(m, "_endpoint", lambda: {"model": endpoint_model})
    return m


@pytest.mark.asyncio
async def test_raw_ainvoke_is_transport_only(monkeypatch):
    captured = {}

    async def fake(**kwargs):
        captured.update(kwargs)
        return _response("raw")

    monkeypatch.setattr("litellm.acompletion", fake)
    model = _model(monkeypatch, "fallback")

    result = await model.raw_ainvoke(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
        tools=TOOLS,
    )

    # 传输层不做提示词注入、不丢弃 tools（适配由 ToolAdapter 负责）
    assert captured["tools"] == TOOLS
    assert captured["messages"][0]["content"] == "sys"
    assert result.content == "raw"


@pytest.mark.asyncio
async def test_raw_stream_is_transport_only(monkeypatch):
    captured = {}

    async def fake_stream_chat(messages, **kwargs):
        captured["messages"] = messages
        captured.update(kwargs)
        return StreamResult(content="ok", tool_calls=None, finish_reason="stop")

    monkeypatch.setattr("models.stream.stream_chat", fake_stream_chat)
    model = _model(monkeypatch, "fallback")

    result = await model.raw_stream(
        [{"role": "system", "content": "sys"}], tools=TOOLS
    )

    assert captured["tools"] == TOOLS
    assert captured["messages"][0]["content"] == "sys"
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_fallback_loop_executes_tool_and_replies(monkeypatch):
    from execution.tool_registry import ToolRegistry
    from graph.nodes.base import run_tool_loop

    calls = {"n": 0}
    seen_messages: list[list[dict]] = []

    async def fake(**kwargs):
        calls["n"] += 1
        seen_messages.append(kwargs["messages"])
        if calls["n"] == 1:
            return _response(
                f"{NAME_OPEN}echo{NAME_CLOSE}\n"
                f'{ARGS_OPEN}{{"text": "hi"}}{ARGS_CLOSE}'
            )
        return _response("工具说 hi")

    monkeypatch.setattr("litellm.acompletion", fake)
    model = _model(monkeypatch, "fallback")

    reg = ToolRegistry()

    def echo(text: str) -> str:
        return f"echo:{text}"

    reg.register_function(
        echo,
        name="echo",
        description="回显",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
    )

    out = await run_tool_loop(
        model, [{"role": "user", "content": "go"}], reg, max_rounds=3
    )

    assert out["text"] == "工具说 hi"
    assert out["tool_trace"][0]["name"] == "echo"
    assert out["tool_trace"][0]["output"] == "echo:hi"
    # 第二次请求里工具结果已文本化（回退模型读得懂）
    assert any("echo:hi" in str(m.get("content")) for m in seen_messages[1])


@pytest.mark.asyncio
async def test_loop_returns_invalid_json_error_to_model(monkeypatch):
    from execution.tool_registry import ToolRegistry
    from graph.nodes.base import run_tool_loop

    calls = {"n": 0}
    seen_messages: list[list[dict]] = []

    async def fake(**kwargs):
        calls["n"] += 1
        seen_messages.append(kwargs["messages"])
        if calls["n"] == 1:
            return _response(
                f"{NAME_OPEN}echo{NAME_CLOSE}\n{ARGS_OPEN}oops{{{{{{{ARGS_CLOSE}"
            )
        return _response("收到错误")

    monkeypatch.setattr("litellm.acompletion", fake)
    model = _model(monkeypatch, "fallback")
    reg = ToolRegistry()
    executed = {"n": 0}

    def echo(text: str) -> str:
        executed["n"] += 1
        return text

    reg.register_function(
        echo,
        name="echo",
        description="回显",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
    )

    out = await run_tool_loop(
        model, [{"role": "user", "content": "go"}], reg, max_rounds=3
    )

    assert executed["n"] == 0
    assert out["text"] == "收到错误"
    assert "不是合法 JSON" in json.dumps(seen_messages[1], ensure_ascii=False)
