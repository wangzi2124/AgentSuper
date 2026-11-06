"""File/directory tasks without tool evidence must not be answered from memory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.nodes.base import run_tool_loop
from harness.tool_adapter import ToolAdapter
from models.router import ChatResult, ToolCall


class _ScriptedModel:
    tier = "fake"

    def __init__(self, script):
        self.script = list(script)

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return self.script.pop(0)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="ls",
            description="list files",
            fn=lambda path=".": "alpha.txt\nbeta.json",
        )
    )
    return registry


def _tool_call() -> ChatResult:
    return ChatResult(
        content="",
        tool_calls=[ToolCall(id="c1", name="ls", arguments=json.dumps({"path": "D:/"}))],
        finish_reason="tool-calls",
    )


@pytest.mark.asyncio
async def test_file_task_without_tool_is_not_fabricated():
    model = _ScriptedModel(
        [
            ChatResult(
                content="D盘有这些文件：abc.txt、def.json、ghi.md",
                finish_reason="stop",
            )
        ]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "在 D盘有哪些文件"}],
        _registry(),
        adapter=ToolAdapter(model, mode="fallback", model_id="ollama_chat/deepseek-r1:7b"),
    )

    assert "无法确认" in out["text"]
    assert "abc.txt" not in out["text"]
    assert not out["tool_trace"]


@pytest.mark.asyncio
async def test_promise_without_tool_is_nudged_then_evidence_is_accepted():
    seen: list[str] = []

    def read_file(path: str) -> str:
        seen.append(path)
        return "# AgentSuper"

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(name="read_file", description="read", fn=read_file)
    )
    model = _ScriptedModel(
        [
            ChatResult(content="我将读取 README.md，请稍等。", finish_reason="stop"),
            ChatResult(
                content="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="read_file",
                        arguments=json.dumps({"path": "README.md"}),
                    )
                ],
                finish_reason="stop",
            ),
            ChatResult(content="README.md 的内容是 # AgentSuper", finish_reason="stop"),
        ]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "先 ls C:\\tool\\AgentSuper，再查看 README.md 写的是什么"}],
        registry,
        adapter=ToolAdapter(model, mode="native"),
    )

    assert seen == ["README.md"]
    assert "无法确认" not in out["text"]
    assert "# AgentSuper" in out["text"]


@pytest.mark.asyncio
async def test_file_task_with_tool_evidence_is_allowed():
    model = _ScriptedModel(
        [
            _tool_call(),
            ChatResult(content="D盘有 alpha.txt、beta.json", finish_reason="stop"),
        ]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "在 D盘有哪些文件"}],
        _registry(),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert out["tool_trace"], out["text"]
    assert "alpha.txt" in out["text"]
    assert "无法确认" not in out["text"]


@pytest.mark.asyncio
async def test_file_task_with_prior_turn_tool_evidence_is_allowed():
    history = [
        {"role": "user", "content": "在 D盘有哪些文件"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c0",
                    "type": "function",
                    "function": {"name": "ls", "arguments": json.dumps({"path": "D:/"})},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c0", "name": "ls", "content": "alpha.txt\nbeta.json"},
        {"role": "user", "content": "再列一次 D盘有哪些文件"},
    ]
    model = _ScriptedModel(
        [ChatResult(content="D盘有 alpha.txt、beta.json", finish_reason="stop")]
    )
    out = await run_tool_loop(
        model,
        history,
        _registry(),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert not out["tool_trace"]
    assert "alpha.txt" in out["text"]
    assert "无法确认" not in out["text"]


@pytest.mark.asyncio
async def test_prior_non_file_tool_is_not_evidence():
    history = [
        {"role": "user", "content": "搜一下 Python GIL"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c0",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": json.dumps({"query": "GIL"})},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c0", "name": "web_search", "content": "GIL 是全局解释器锁。"},
        {"role": "user", "content": "在 D盘有哪些文件"},
    ]
    model = _ScriptedModel(
        [ChatResult(content="D盘有这些文件：abc.txt、def.json", finish_reason="stop")]
    )
    out = await run_tool_loop(
        model,
        history,
        _registry(),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert "无法确认" in out["text"]
    assert "abc.txt" not in out["text"]


@pytest.mark.asyncio
async def test_missing_ls_path_is_inferred_from_user_message():
    seen: dict[str, str] = {}

    def ls(path: str = ".") -> str:
        seen["path"] = path
        return "alpha.txt"

    registry = ToolRegistry()
    registry.register(ToolDefinition(name="ls", description="list files", fn=ls))
    model = _ScriptedModel(
        [
            ChatResult(
                content="",
                tool_calls=[ToolCall(id="c1", name="ls", arguments="{}")],
                finish_reason="tool-calls",
            ),
            ChatResult(content=r"c:\tool 下有 alpha.txt", finish_reason="stop"),
        ]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": r"在 c:\tool 这个文件夹 有什么信息"}],
        registry,
        adapter=ToolAdapter(model, mode="native"),
    )

    assert seen["path"] == r"c:\tool"
    assert out["tool_trace"]
    calls = [
        m for m in out["tool_history"] if m["role"] == "assistant"
    ][-1]["tool_calls"]
    assert json.loads(calls[0]["function"]["arguments"])["path"] == r"c:\tool"


@pytest.mark.asyncio
async def test_relative_path_follows_prior_directory_and_reads_directory_as_listing():
    seen: dict[str, str] = {}

    def read_file(path: str) -> str:
        seen["read"] = path
        return f"错误：{path} 是目录，请用 ls"

    def ls(path: str = ".") -> str:
        seen["ls"] = path
        return "a.txt\nb.txt"

    registry = ToolRegistry()
    registry.register(ToolDefinition(name="read_file", description="read", fn=read_file))
    registry.register(ToolDefinition(name="ls", description="list", fn=ls))
    model = _ScriptedModel(
        [
            ChatResult(
                content="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="read_file",
                        arguments=json.dumps({"path": "Tesseract-OCR"}),
                    )
                ],
                finish_reason="tool-calls",
            ),
            ChatResult(content="Tesseract-OCR 下有 a.txt、b.txt", finish_reason="stop"),
        ]
    )
    out = await run_tool_loop(
        model,
        [
            {"role": "user", "content": r"在 c:\tool 路径下 有什么"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c0",
                        "type": "function",
                        "function": {"name": "ls", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c0", "name": "ls", "content": "Tesseract-OCR/"},
            {"role": "user", "content": "在这个目录下 Tesseract-OCR 文件夹有什么"},
        ],
        registry,
        adapter=ToolAdapter(model, mode="native"),
    )

    expected = str(Path(r"c:\tool") / "Tesseract-OCR")
    assert seen["read"] == expected
    assert seen["ls"] == expected
    assert "a.txt" in out["text"]
    calls = [
        m for m in out["tool_history"] if m["role"] == "assistant"
    ][-1]["tool_calls"]
    assert json.loads(calls[0]["function"]["arguments"])["path"] == expected


@pytest.mark.asyncio
async def test_non_file_task_without_tool_is_not_blocked():
    model = _ScriptedModel(
        [ChatResult(content="GIL 是 Python 的全局解释器锁。", finish_reason="stop")]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "解释一下 Python 的 GIL"}],
        _registry(),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert "GIL" in out["text"]
    assert "无法确认" not in out["text"]
