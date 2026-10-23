import json
from types import SimpleNamespace

import pytest

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
    monkeypatch.setenv("HARNESS_TOOL_MODE", mode)
    m = TierModel(tier="auto")
    monkeypatch.setattr(m, "_endpoint", lambda: {"model": endpoint_model})
    return m


@pytest.mark.asyncio
async def test_fallback_call_injects_prompt_and_parses(monkeypatch):
    captured = {}

    async def fake(**kwargs):
        captured.update(kwargs)
        return _response(
            f"我来回显。\n{NAME_OPEN}tool_echo{NAME_CLOSE}\n"
            f'{ARGS_OPEN}{{"text": "hi"}}{ARGS_CLOSE}'
        )

    monkeypatch.setattr("litellm.acompletion", fake)
    model = _model(monkeypatch, "fallback")

    result = await model.ainvoke([{"role": "user", "content": "go"}], tools=TOOLS)

    assert "tools" not in captured
    system_text = captured["messages"][0]["content"]
    assert "tool_echo" in system_text and "ꊰ▸function_name◂ꊰ" in system_text
    assert result.tool_calls[0].name == "tool_echo"
    assert json.loads(result.tool_calls[0].arguments) == {"text": "hi"}
    assert result.content == "我来回显。"


@pytest.mark.asyncio
async def test_native_keeps_tools_and_normalizes_history(monkeypatch):
    captured = {}

    async def fake(**kwargs):
        captured.update(kwargs)
        return _response("ok")

    monkeypatch.setattr("litellm.acompletion", fake)
    model = _model(monkeypatch, "native")

    result = await model.ainvoke(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "tool_echo", "arguments": {"text": "x"}}],
            },
            {"role": "tool", "tool_call_id": "", "content": "done"},
            {"role": "user", "content": "继续"},
        ],
        tools=TOOLS,
    )

    assert captured["tools"] == TOOLS
    assert captured["messages"][0]["tool_calls"][0]["function"]["arguments"] == '{"text": "x"}'
    # 孤儿 tool 消息被文本化，不会让厂商 400
    assert captured["messages"][1]["role"] == "user"
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_fallback_bad_json_kept_for_repair(monkeypatch):
    async def fake(**kwargs):
        return _response(
            f"{NAME_OPEN}tool_echo{NAME_CLOSE}\n{ARGS_OPEN}not-json{ARGS_CLOSE}"
        )

    monkeypatch.setattr("litellm.acompletion", fake)
    model = _model(monkeypatch, "fallback")
    result = await model.ainvoke([{"role": "user", "content": "go"}], tools=TOOLS)
    assert result.tool_calls[0].arguments == "not-json"


@pytest.mark.asyncio
async def test_stream_fallback_filters_delimiters_and_emits_calls(monkeypatch):
    text = (
        "稍等。"
        f"{NAME_OPEN}tool_echo{NAME_CLOSE}\n"
        f'{ARGS_OPEN}{{"text": "hi"}}{ARGS_CLOSE}'
    )

    async def fake_stream_chat(messages, *, on_text=None, **kwargs):
        assert kwargs.get("tools") is None
        for piece in (text[:3], text[3:11], text[11:]):
            if on_text is not None:
                on_text(piece)
        return StreamResult(content=text, tool_calls=None, finish_reason="stop")

    monkeypatch.setattr("models.stream.stream_chat", fake_stream_chat)
    model = _model(monkeypatch, "fallback")
    seen: list[str] = []

    result = await model.astream(
        [{"role": "user", "content": "go"}],
        tools=TOOLS,
        on_text=seen.append,
    )

    assert "".join(seen) == "稍等。"
    assert result.content == "稍等。"
    assert result.tool_calls[0].name == "tool_echo"
    assert json.loads(result.tool_calls[0].arguments) == {"text": "hi"}


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
