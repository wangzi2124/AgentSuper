"""agent-4 §4/§6-P2：与模型无关的 PreToolUse / PostToolUse Hook。"""

from __future__ import annotations

import pytest

from execution.tool_registry import ToolRegistry
from graph.nodes.base import run_tool_loop
from harness.hooks import ToolHook, reset_tool_hooks, run_post_tool, run_pre_tool, set_tool_hooks
from models.types import ChatResult, ToolCall


def test_hooks_chain_and_isolate():
    token = set_tool_hooks(
        [
            ToolHook(
                pre=lambda _name, args: {**args, "a": args.get("a", 0) + 1},
                post=lambda _name, _args, out: out.upper(),
            ),
            ToolHook(pre=lambda _name, args: {**args, "b": 2}),
        ]
    )
    try:
        assert run_pre_tool("t", {"a": 0}) == {"a": 1, "b": 2}
        assert run_post_tool("t", {}, "ok") == "OK"
    finally:
        reset_tool_hooks(token)
    assert run_pre_tool("t", {"a": 0}) == {"a": 0}
    assert run_post_tool("t", {}, "ok") == "ok"


def test_hook_errors_never_break_loop():
    def boom(*_args):
        raise RuntimeError("hook failed")

    token = set_tool_hooks([ToolHook(pre=boom, post=boom)])
    try:
        assert run_pre_tool("t", {"a": 1}) == {"a": 1}
        assert run_post_tool("t", {}, "ok") == "ok"
    finally:
        reset_tool_hooks(token)


class _ToolThenAnswer:
    name = "fake-model"
    tier = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return ChatResult(
                content="",
                tool_calls=[ToolCall(id="c1", name="noop", arguments='{"a": 1}')],
                finish_reason="tool-calls",
            )
        return ChatResult(content="done", finish_reason="stop")


@pytest.mark.asyncio
async def test_hooks_wrap_real_tool_execution():
    seen: list[dict] = []
    reg = ToolRegistry()
    reg.register_function(
        lambda a: f"raw:{a}",
        name="noop",
        description="test",
        parameters={
            "type": "object",
            "properties": {"a": {"type": "integer"}},
            "required": ["a"],
        },
    )

    def pre(_name, args):
        seen.append(dict(args))
        return {**args, "a": args["a"] + 10}

    token = set_tool_hooks([ToolHook(pre=pre, post=lambda _n, _a, out: f"[hooked]{out}")])
    try:
        out = await run_tool_loop(
            _ToolThenAnswer(), [{"role": "user", "content": "go"}], reg
        )
    finally:
        reset_tool_hooks(token)

    assert seen == [{"a": 1}]
    assert out["tool_trace"][0]["args"] == {"a": 11}
    assert out["tool_trace"][0]["output"] == "[hooked]raw:11"
    assert out["text"] == "done"
