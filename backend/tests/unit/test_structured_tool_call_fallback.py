from __future__ import annotations

import json

import pytest
from config import settings
from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.nodes.base import _json_object_candidate, run_tool_loop
from harness.tool_adapter import ToolAdapter
from models.types import ChatResult

ACTION_USER = r"帮我把这个代码写到 C:\tool\code\dpn-platform-bciblp 目录下"


class _LocalOllamaModel:
    tier = "fake"

    def __init__(self, script, *, local: bool = True):
        self.script = list(script)
        self.local = local
        self.complete_kwargs: list[dict] = []
        self.stream_kwargs: list[dict] = []

    def model_name(self) -> str:
        return "ollama_chat/qwen2.5:3b"

    def _endpoint(self) -> dict:
        return {
            "api_base": "http://127.0.0.1:11434" if self.local else "https://cloud.example"
        }

    def _is_local_ollama(self, endpoint: dict) -> bool:
        return self.local

    async def raw_ainvoke(self, messages, *, tools=None, **kwargs):
        self.complete_kwargs.append(kwargs)
        return self.script.pop(0)

    async def raw_stream(self, messages, *, tools=None, on_text=None, **kwargs):
        self.stream_kwargs.append(kwargs)
        return self.script.pop(0)


def _registry(sink: dict | None = None) -> ToolRegistry:
    def _write(**kwargs):
        if sink is not None:
            sink.update(kwargs)
        return "written"

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="write_file",
            description="write file",
            fn=_write,
        )
    )
    return registry


def _adapter(model: _LocalOllamaModel) -> ToolAdapter:
    return ToolAdapter(model, mode="fallback", model_id="ollama_chat/qwen2.5:3b")


def _structured_call() -> ChatResult:
    return ChatResult(
        content=json.dumps(
            {
                "name": "write_file",
                "arguments": {
                    "path": "C:/tool/code/dpn-platform-bciblp/BubbleSort.java",
                    "content": "class BubbleSort {}",
                },
            },
            ensure_ascii=False,
        ),
        finish_reason="stop",
    )


def _structured_call_with_fenced_code() -> ChatResult:
    return ChatResult(
        content=json.dumps(
            {
                "name": "write_file",
                "arguments": {
                    "path": "C:/tool/code/dpn-platform-bciblp/BubbleSort.java",
                    "content": "```java\nclass BubbleSort {}\n```",
                },
            },
            ensure_ascii=False,
        ),
        finish_reason="stop",
    )


def _response_format(kwargs: dict) -> dict:
    return kwargs["response_format"]["json_schema"]["schema"]


@pytest.mark.parametrize(
    "text",
    [
        '```json\n{"name": "write_file", "arguments": {}}\n```',
        '结果如下：\n```json\n{"name": "write_file", "arguments": {}}\n```',
    ],
)
def test_json_object_candidate_accepts_fenced_json(text):
    assert _json_object_candidate(text)["name"] == "write_file"


def test_json_object_candidate_ignores_fence_inside_json_argument():
    parsed = _json_object_candidate(_structured_call_with_fenced_code().content)
    assert parsed["name"] == "write_file"
    assert "```java" in parsed["arguments"]["content"]


@pytest.mark.asyncio
async def test_structured_fallback_parses_json_with_inner_code_fence():
    model = _LocalOllamaModel(
        [_structured_call_with_fenced_code(), ChatResult(content="已写入", finish_reason="stop")]
    )
    captured: dict = {}
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": ACTION_USER}],
        _registry(captured),
        adapter=_adapter(model),
    )

    assert [trace["name"] for trace in out["tool_trace"]] == ["write_file"]
    assert captured["content"] == "```java\nclass BubbleSort {}\n```"
    assert out["text"] == "已写入"


@pytest.mark.asyncio
async def test_structured_fallback_converts_json_to_tool_call():
    model = _LocalOllamaModel(
        [_structured_call(), ChatResult(content="已写入 BubbleSort.java", finish_reason="stop")]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": ACTION_USER}],
        _registry(),
        adapter=_adapter(model),
    )

    assert [trace["name"] for trace in out["tool_trace"]] == ["write_file"]
    assert out["text"] == "已写入 BubbleSort.java"
    assert _response_format(model.complete_kwargs[0])["properties"]["name"]["enum"] == [
        "write_file"
    ]
    assert "response_format" not in model.complete_kwargs[1]


@pytest.mark.asyncio
async def test_structured_fallback_forces_nonstream_round():
    model = _LocalOllamaModel(
        [_structured_call(), ChatResult(content="已写入", finish_reason="stop")]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": ACTION_USER}],
        _registry(),
        adapter=_adapter(model),
        stream=True,
    )

    assert len(model.complete_kwargs) == 1
    assert "response_format" in model.complete_kwargs[0]
    assert model.stream_kwargs == [{}]
    assert [trace["name"] for trace in out["tool_trace"]] == ["write_file"]
    assert out["text"] == "已写入"


@pytest.mark.asyncio
async def test_structured_fallback_disabled_by_config(monkeypatch):
    monkeypatch.setattr(settings, "harness_structured_tool_call_fallback_enabled", False)
    model = _LocalOllamaModel(
        [_structured_call(), ChatResult(content="普通回答", finish_reason="stop")]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": ACTION_USER}],
        _registry(),
        adapter=_adapter(model),
    )

    assert "response_format" not in model.complete_kwargs[0]
    assert not out["tool_trace"]
    assert "还没有实际执行" in out["text"]


@pytest.mark.asyncio
async def test_structured_fallback_skips_non_local_ollama(monkeypatch):
    monkeypatch.setattr(
        settings, "harness_structured_tool_call_fallback_local_ollama_only", True
    )
    model = _LocalOllamaModel(
        [_structured_call(), ChatResult(content="普通回答", finish_reason="stop")],
        local=False,
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": ACTION_USER}],
        _registry(),
        adapter=_adapter(model),
    )

    assert "response_format" not in model.complete_kwargs[0]
    assert not out["tool_trace"]
    assert "还没有实际执行" in out["text"]


@pytest.mark.asyncio
async def test_structured_fallback_can_include_non_local_models(monkeypatch):
    monkeypatch.setattr(
        settings, "harness_structured_tool_call_fallback_local_ollama_only", False
    )
    model = _LocalOllamaModel(
        [_structured_call(), ChatResult(content="已写入", finish_reason="stop")],
        local=False,
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": ACTION_USER}],
        _registry(),
        adapter=_adapter(model),
    )

    assert "response_format" in model.complete_kwargs[0]
    assert [trace["name"] for trace in out["tool_trace"]] == ["write_file"]
