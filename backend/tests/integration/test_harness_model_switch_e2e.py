"""验收：模型 A（原生）→ 模型 B（文本回退）的会话历史仍可继续（agent-4 §7）。"""

import json
from types import SimpleNamespace

import pytest

from harness.delimiters import NAME_OPEN, parse_tool_calls
from models.router import TierModel, reset_request_model, set_request_model

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "tool_read_file",
            "description": "读文件",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }
]


def _native_response():
    call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(
            name="tool_read_file", arguments=json.dumps({"path": "a.txt"})
        ),
    )
    msg = SimpleNamespace(content="", tool_calls=[call])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")],
        usage=None,
        model="native-model",
    )


def _text_response(text):
    msg = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason="stop")],
        usage=None,
        model="fallback-model",
    )


@pytest.mark.asyncio
async def test_native_history_works_after_switch_to_fallback_model(monkeypatch):
    monkeypatch.delenv("HARNESS_TOOL_MODE", raising=False)
    monkeypatch.setenv("HARNESS_NATIVE_MODELS", "native-model")
    monkeypatch.setenv("HARNESS_FALLBACK_MODELS", "fallback-model")
    monkeypatch.setattr(
        TierModel, "_endpoint", lambda self: {"model": self._direct_model()}
    )

    captured: list[dict] = []

    async def fake(**kwargs):
        captured.append(kwargs)
        if len(captured) == 1:
            return _native_response()
        return _text_response("文件内容是 hello")

    monkeypatch.setattr("litellm.acompletion", fake)

    model = TierModel(tier="auto")

    token = set_request_model("native-model")
    try:
        first = await model.ainvoke(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "读 a.txt"},
            ],
            tools=TOOLS,
        )
    finally:
        reset_request_model(token)

    assert first.tool_calls and first.tool_calls[0].name == "tool_read_file"
    assert captured[0]["tools"] == TOOLS

    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "读 a.txt"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": first.tool_calls[0].id,
                    "type": "function",
                    "function": {
                        "name": first.tool_calls[0].name,
                        "arguments": first.tool_calls[0].arguments,
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": first.tool_calls[0].id,
            "content": "hello",
        },
    ]

    token = set_request_model("fallback-model")
    try:
        second = await model.ainvoke(history, tools=TOOLS)
    finally:
        reset_request_model(token)

    assert second.content == "文件内容是 hello"
    fallback_req = captured[1]
    assert "tools" not in fallback_req
    assert all(m["role"] != "tool" for m in fallback_req["messages"])
    assert all("tool_calls" not in m for m in fallback_req["messages"])
    assistant_text = next(
        m["content"] for m in fallback_req["messages"] if m["role"] == "assistant"
    )
    assert NAME_OPEN in assistant_text
    calls, _ = parse_tool_calls(assistant_text)
    assert calls[0].parsed == {"path": "a.txt"}
    # 工具结果文本化后以 user 身份出现在历史里
    assert any(
        m["role"] == "user" and "hello" in str(m["content"])
        for m in fallback_req["messages"]
    )
