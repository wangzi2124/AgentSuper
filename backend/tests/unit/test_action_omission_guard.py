from __future__ import annotations

import json

import pytest
from config import settings
from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.nodes.base import run_tool_loop
from harness.tool_adapter import ToolAdapter
from models.router import ChatResult, ToolCall

ACTION_USER = r"帮我把这个代码写到 C:\tool\code\dpn-platform-bciblp 目录下"


class _ScriptedModel:
    tier = "fake"

    def __init__(self, script, fallback=None):
        self.script = list(script)
        self.fallback = fallback
        self.calls = 0

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls += 1
        if self.script:
            return self.script.pop(0)
        if self.fallback is not None:
            return self.fallback
        return ChatResult(content="", finish_reason="stop")


def _registry(*names):
    registry = ToolRegistry()
    for name in names:
        registry.register(
            ToolDefinition(
                name=name,
                description=name,
                fn=lambda **kwargs: f"{name}:ok",
            )
        )
    return registry


def _promise():
    return ChatResult(
        content="好的，我会将代码写入目标目录。请稍等，我将执行这些步骤。",
        finish_reason="stop",
    )


def _write_call():
    return ChatResult(
        content="",
        tool_calls=[
            ToolCall(
                id="c1",
                name="write_file",
                arguments=json.dumps(
                    {
                        "path": "C:/tool/code/dpn-platform-bciblp/BubbleSort.java",
                        "content": "class BubbleSort {}",
                    }
                ),
            )
        ],
        finish_reason="tool-calls",
    )


def _read_call():
    return ChatResult(
        content="",
        tool_calls=[
            ToolCall(
                id="c0",
                name="ls",
                arguments=json.dumps({"path": "C:/tool/code"}),
            )
        ],
        finish_reason="tool-calls",
    )


@pytest.mark.asyncio
async def test_action_promise_is_nudged_then_tool_runs():
    model = _ScriptedModel(
        [
            _promise(),
            _write_call(),
            ChatResult(content="已写入 BubbleSort.java", finish_reason="stop"),
        ]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": ACTION_USER}],
        _registry("write_file"),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert [trace["name"] for trace in out["tool_trace"]] == ["write_file"]
    assert "已写入" in out["text"]
    assert model.calls == 3


@pytest.mark.asyncio
async def test_action_promise_without_tool_call_returns_explicit_failure():
    model = _ScriptedModel([_promise()], fallback=_promise())
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": ACTION_USER}],
        _registry("write_file"),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert "还没有实际执行" in out["text"]
    assert "无法确认" in out["text"]
    assert not out["tool_trace"]
    assert model.calls == 2


@pytest.mark.asyncio
async def test_non_action_task_with_promise_wording_is_not_nudged():
    model = _ScriptedModel(
        [ChatResult(content="好的，我将简要说明如下：GIL 是全局解释器锁。", finish_reason="stop")]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "解释一下 Python 的 GIL"}],
        _registry("write_file"),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert "GIL" in out["text"]
    assert "还没有实际执行" not in out["text"]
    assert model.calls == 1


@pytest.mark.asyncio
async def test_generate_code_without_file_target_is_not_gated():
    model = _ScriptedModel(
        [ChatResult(content="好的，我将生成一段示例代码：print('hi')", finish_reason="stop")]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "生成一段代码"}],
        _registry("write_file"),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert "print('hi')" in out["text"]
    assert "还没有实际执行" not in out["text"]
    assert model.calls == 1


@pytest.mark.asyncio
async def test_action_task_without_mutating_tool_keeps_existing_behavior():
    model = _ScriptedModel([_promise()], fallback=_promise())
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": ACTION_USER}],
        _registry("ls"),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert "还没有实际执行" not in out["text"]
    assert "我会将代码写入目标目录" in out["text"]
    assert model.calls == 1


@pytest.mark.asyncio
async def test_read_tool_evidence_does_not_satisfy_write_task():
    model = _ScriptedModel([_read_call(), _promise()], fallback=_promise())
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": ACTION_USER}],
        _registry("ls", "write_file"),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert [trace["name"] for trace in out["tool_trace"]] == ["ls"]
    assert "还没有实际执行" in out["text"]
    assert model.calls == 3


@pytest.mark.asyncio
async def test_guard_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "harness_action_omission_guard_enabled", False)
    model = _ScriptedModel([_promise()], fallback=_promise())
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": ACTION_USER}],
        _registry("write_file"),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert "我会将代码写入目标目录" in out["text"]
    assert "还没有实际执行" not in out["text"]
    assert model.calls == 1


@pytest.mark.asyncio
async def test_nudge_max_zero_skips_nudge(monkeypatch):
    monkeypatch.setattr(settings, "harness_action_omission_nudge_max", 0)
    model = _ScriptedModel([_promise()], fallback=_promise())
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": ACTION_USER}],
        _registry("write_file"),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert "还没有实际执行" in out["text"]
    assert model.calls == 1


@pytest.mark.asyncio
async def test_nudge_max_two_allows_two_corrections(monkeypatch):
    monkeypatch.setattr(settings, "harness_action_omission_nudge_max", 2)
    model = _ScriptedModel(
        [
            _promise(),
            _promise(),
            _write_call(),
            ChatResult(content="已写入", finish_reason="stop"),
        ]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": ACTION_USER}],
        _registry("write_file"),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert [trace["name"] for trace in out["tool_trace"]] == ["write_file"]
    assert model.calls == 4
