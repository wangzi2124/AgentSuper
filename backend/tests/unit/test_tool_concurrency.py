"""Unit tests: same-round tool calls run concurrently with bounded, safe policies."""

from __future__ import annotations

import asyncio

import pytest

from config import settings
from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.nodes.base import (
    reset_permission_hook,
    run_tool_loop,
    set_permission_hook,
)
from models.router import ChatResult, ToolCall


class _MultiToolModel:
    tier = "fake"

    def __init__(self, calls: list[tuple[str, str]]) -> None:
        self._calls = calls
        self.rounds = 0

    def model_name(self) -> str:
        return "multi-tool"

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.rounds += 1
        if self.rounds == 1:
            return ChatResult(
                content="",
                tool_calls=[
                    ToolCall(id=f"c{i}", name=name, arguments="{}")
                    for i, (name, _) in enumerate(self._calls)
                ],
                finish_reason="tool-calls",
            )
        return ChatResult(content="done", finish_reason="stop")


def _run(calls, tools: dict[str, ToolDefinition]):
    registry = ToolRegistry()
    for td in tools.values():
        registry.register(td)
    return run_tool_loop(
        _MultiToolModel(calls),
        [{"role": "user", "content": "go"}],
        registry,
    )


@pytest.mark.asyncio
async def test_two_read_tools_start_before_either_finishes():
    started: list[str] = []
    barrier = asyncio.Event()

    async def make(label: str) -> str:
        started.append(label)
        if len(started) >= 2:
            barrier.set()
        await asyncio.wait_for(barrier.wait(), timeout=1.0)
        return label

    tools = {
        "tool_a": ToolDefinition(name="tool_a", description="", fn=lambda: make("a")),
        "tool_b": ToolDefinition(name="tool_b", description="", fn=lambda: make("b")),
    }
    out = await _run([("tool_a", "a"), ("tool_b", "b")], tools)

    assert started == ["a", "b"]
    assert [t["output"] for t in out["tool_trace"]] == ["a", "b"]


@pytest.mark.asyncio
async def test_tool_concurrency_setting_bounds_parallelism(monkeypatch):
    monkeypatch.setattr(settings, "tool_concurrency", 1)
    active = {"now": 0, "max": 0}

    async def slow(label: str) -> str:
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        try:
            await asyncio.sleep(0.02)
            return label
        finally:
            active["now"] -= 1

    tools = {
        "tool_a": ToolDefinition(name="tool_a", description="", fn=lambda: slow("a")),
        "tool_b": ToolDefinition(name="tool_b", description="", fn=lambda: slow("b")),
    }
    await _run([("tool_a", "a"), ("tool_b", "b")], tools)
    assert active["max"] == 1


@pytest.mark.asyncio
async def test_write_and_shell_are_serialized_while_reads_stay_parallel():
    mutate_state = {"now": 0, "max": 0}
    read_state = {"now": 0, "max": 0}
    workspace = {"readers": 0, "writers": 0, "overlap": False}

    async def mutate(label: str) -> str:
        if workspace["readers"] or workspace["writers"]:
            workspace["overlap"] = True
        workspace["writers"] += 1
        mutate_state["now"] += 1
        mutate_state["max"] = max(mutate_state["max"], mutate_state["now"])
        try:
            await asyncio.sleep(0.03)
            return label
        finally:
            mutate_state["now"] -= 1
            workspace["writers"] -= 1

    async def read(label: str) -> str:
        if workspace["writers"]:
            workspace["overlap"] = True
        workspace["readers"] += 1
        read_state["now"] += 1
        read_state["max"] = max(read_state["max"], read_state["now"])
        try:
            await asyncio.sleep(0.03)
            return label
        finally:
            read_state["now"] -= 1
            workspace["readers"] -= 1

    tools = {
        "write_file": ToolDefinition(name="write_file", description="", fn=lambda: mutate("w")),
        "edit_file": ToolDefinition(name="edit_file", description="", fn=lambda: mutate("e")),
        "read_file": ToolDefinition(name="read_file", description="", fn=lambda: read("ra")),
        "grep": ToolDefinition(name="grep", description="", fn=lambda: read("rb")),
    }
    out = await _run(
        [
            ("write_file", "w"),
            ("read_file", "ra"),
            ("edit_file", "e"),
            ("grep", "rb"),
        ],
        tools,
    )
    assert mutate_state["max"] == 1
    assert read_state["max"] == 2
    assert workspace["overlap"] is False
    assert [t["name"] for t in out["tool_trace"]] == [
        "write_file",
        "read_file",
        "edit_file",
        "grep",
    ]


@pytest.mark.asyncio
async def test_custom_tool_mutating_flag_controls_serialization():
    active = {"now": 0, "max": 0}

    async def work(label: str) -> str:
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        try:
            await asyncio.sleep(0.02)
            return label
        finally:
            active["now"] -= 1

    writer = {
        "custom_writer_a": ToolDefinition(
            name="custom_writer_a", description="", fn=lambda: work("a"), mutating=True
        ),
        "custom_writer_b": ToolDefinition(
            name="custom_writer_b", description="", fn=lambda: work("b"), mutating=True
        ),
    }
    await _run([("custom_writer_a", "a"), ("custom_writer_b", "b")], writer)
    assert active["max"] == 1

    active["max"] = 0
    readonly = {
        "custom_reader_a": ToolDefinition(
            name="custom_reader_a", description="", fn=lambda: work("a"), mutating=False
        ),
        "custom_reader_b": ToolDefinition(
            name="custom_reader_b", description="", fn=lambda: work("b"), mutating=False
        ),
    }
    await _run([("custom_reader_a", "a"), ("custom_reader_b", "b")], readonly)
    assert active["max"] == 2


@pytest.mark.asyncio
async def test_approval_prompts_are_serialized():
    active = {"now": 0, "max": 0}

    async def hook(tool: str, args: dict) -> bool:
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        try:
            await asyncio.sleep(0.02)
            return True
        finally:
            active["now"] -= 1

    async def work(label: str) -> str:
        return label

    tools = {
        "tool_a": ToolDefinition(
            name="tool_a", description="", fn=lambda: work("a"), require_approval=True
        ),
        "tool_b": ToolDefinition(
            name="tool_b", description="", fn=lambda: work("b"), require_approval=True
        ),
    }
    token = set_permission_hook(hook)
    try:
        out = await _run([("tool_a", "a"), ("tool_b", "b")], tools)
    finally:
        reset_permission_hook(token)

    assert active["max"] == 1
    assert [t["output"] for t in out["tool_trace"]] == ["a", "b"]
