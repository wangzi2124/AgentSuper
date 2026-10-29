import asyncio
import json

import pytest

from config import settings
from harness.delimiters import ARGS_CLOSE, ARGS_OPEN, NAME_CLOSE, NAME_OPEN
from harness.tool_adapter import (
    Done,
    TextDelta,
    ToolAdapter,
    ToolCallReady,
    get_tool_adapter,
)
from models.stream import StreamResult
from models.types import ChatResult, ToolCall

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

CALL_TEXT = (
    f"{NAME_OPEN}tool_echo{NAME_CLOSE}\n"
    f'{ARGS_OPEN}{{"text": "hi"}}{ARGS_CLOSE}'
)


class _RawModel:
    name = "fake-model"

    def __init__(self, result=None, stream=None):
        self.result = result or ChatResult(content="ok")
        self._stream = stream
        self.ainvoke_calls: list[tuple] = []
        self.raw_calls: list[tuple] = []
        self.stream_calls: list[tuple] = []

    async def raw_ainvoke(self, messages, *, tools=None):
        self.raw_calls.append((messages, tools))
        return self.result

    async def ainvoke(self, messages, *, tools=None):
        self.ainvoke_calls.append((messages, tools))
        return self.result

    async def raw_stream(self, messages, *, tools=None, on_text=None):
        self.stream_calls.append((messages, tools))
        for piece in self._stream or []:
            if on_text:
                on_text(piece)
        return StreamResult(content="".join(self._stream or []), finish_reason="stop")


class _LegacyModel:
    name = "legacy-model"

    def __init__(self, result):
        self.result = result

    async def ainvoke(self, messages, *, tools=None):
        return self.result


@pytest.mark.asyncio
async def test_complete_fallback_injects_prompt_and_parses():
    model = _RawModel(ChatResult(content="我来回显。\n" + CALL_TEXT))
    adapter = ToolAdapter(model, mode="fallback")

    result = await adapter.complete([{"role": "user", "content": "go"}], TOOLS)

    messages, tools = model.raw_calls[0]
    assert tools is None
    assert "tool_echo" in messages[0]["content"]
    assert result.content == "我来回显。"
    assert result.tool_calls[0].name == "tool_echo"
    assert json.loads(result.tool_calls[0].arguments) == {"text": "hi"}


@pytest.mark.asyncio
async def test_complete_native_keeps_tools_and_normalizes_history():
    model = _RawModel(ChatResult(content="ok"))
    adapter = ToolAdapter(model, mode="native")

    await adapter.complete(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "tool_echo", "arguments": {"text": "x"}}],
            },
            {"role": "tool", "tool_call_id": "", "content": "done"},
        ],
        TOOLS,
    )

    messages, tools = model.raw_calls[0]
    assert tools == TOOLS
    assert messages[0]["tool_calls"][0]["function"]["arguments"] == '{"text": "x"}'
    assert messages[1]["role"] == "user"


@pytest.mark.asyncio
async def test_legacy_model_without_raw_methods_still_works():
    model = _LegacyModel(
        ChatResult(
            content="ok",
            tool_calls=[ToolCall(id="1", name="tool_echo", arguments="{}")],
        )
    )
    adapter = ToolAdapter(model, mode="native")
    result = await adapter.complete([{"role": "user", "content": "go"}], TOOLS)
    assert result.tool_calls[0].name == "tool_echo"


@pytest.mark.asyncio
async def test_stream_fallback_emits_text_and_call_before_done():
    model = _RawModel(stream=["稍等。", CALL_TEXT[:8], CALL_TEXT[8:]])
    adapter = ToolAdapter(model, mode="fallback")

    events = [e async for e in adapter.stream([{"role": "user", "content": "go"}], TOOLS)]

    texts = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert texts == "稍等。"
    call_idx = next(i for i, e in enumerate(events) if isinstance(e, ToolCallReady))
    done_idx = next(i for i, e in enumerate(events) if isinstance(e, Done))
    assert call_idx < done_idx
    assert events[call_idx].call.name == "tool_echo"
    done = events[-1]
    assert isinstance(done, Done)
    assert done.response.content == "稍等。"
    assert done.response.tool_calls[0].name == "tool_echo"


@pytest.mark.asyncio
async def test_stream_fallback_emits_call_before_response_finishes():
    """定界符闭合即触发，不等待整段响应结束（agent-4 §7）。"""

    class _GatedModel:
        name = "fake-model"

        def __init__(self):
            self.gate = asyncio.Event()
            self.finished = False

        async def raw_stream(self, messages, *, tools=None, on_text=None):
            on_text("思考中。")
            on_text(CALL_TEXT)
            await self.gate.wait()
            on_text("继续说明。")
            self.finished = True
            return StreamResult(
                content="思考中。" + CALL_TEXT + "继续说明。", finish_reason="stop"
            )

    model = _GatedModel()
    adapter = ToolAdapter(model, mode="fallback")
    events = []
    async for event in adapter.stream([{"role": "user", "content": "go"}], TOOLS):
        events.append(event)
        if isinstance(event, ToolCallReady):
            assert not model.gate.is_set()
            assert not model.finished
            assert not any(isinstance(e, Done) for e in events)
            model.gate.set()

    assert model.finished
    texts = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert texts == "思考中。继续说明。"
    assert events[-1].response.tool_calls[0].name == "tool_echo"


@pytest.mark.asyncio
async def test_stream_native_suppresses_prelude_before_embedded_call():
    payload = '{"name": "tool_echo", "arguments": {"text": "hi"}}'
    model = _RawModel(stream=["先解释一下", "，然后调用。", payload])
    adapter = ToolAdapter(model, mode="native")

    events = [e async for e in adapter.stream([{"role": "user", "content": "go"}], TOOLS)]

    texts = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert "先解释" not in texts
    done = events[-1]
    assert isinstance(done, Done)
    assert done.response.tool_calls[0].name == "tool_echo"
    assert json.loads(done.response.tool_calls[0].arguments) == {"text": "hi"}


@pytest.mark.asyncio
async def test_stream_plain_text_flushes_on_done():
    model = _RawModel(stream=["普通", "回答"])
    adapter = ToolAdapter(model, mode="native")

    events = [e async for e in adapter.stream([{"role": "user", "content": "go"}], TOOLS)]

    texts = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert texts.startswith("普通")
    assert "回答" in texts


def test_adapter_infers_model_id_and_mode(monkeypatch):
    monkeypatch.setattr(settings, "harness_fallback_models", ["fake-model"])
    adapter = get_tool_adapter(_RawModel())
    assert adapter.model_id == "fake-model"
    assert adapter.mode == "fallback"
