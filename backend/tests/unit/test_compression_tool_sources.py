from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cognition.compressor import (
    enforce_precision,
    extract_precision_values,
    serialize_messages,
)
from harness.compression import compress_messages

PATH = r"E:\AgentSuper\backend\harness\compression.py"
COMMAND = "pytest tests/unit -q --cov=harness"


def _assistant_tool(call_id: str, name: str, args: dict) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }
        ],
    }


def _tool(call_id: str, name: str, content: str) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "tool_name": name,
        "content": content,
    }


def _mixed_history() -> list[dict]:
    skill_body = (
        "## TDD workflow\n"
        f"- Read `{PATH}` before editing.\n"
        f"- Run `{COMMAND}`.\n"
        "- Never delete the backup.\n" + ("skill guidance line\n" * 200)
    )
    return [
        {
            "role": "user",
            "content": (
                f"请检查 {PATH}，会话号 864213，错误 E_ADDR_IN_USE，"
                f"复现命令 `{COMMAND}`。先别删那个备份。"
            ),
        },
        _assistant_tool("fs1", "read_file", {"path": PATH, "offset": 1}),
        _tool("fs1", "read_file", f"def compress_messages(...): ...\ntoken=864213\n{PATH}"),
        _assistant_tool(
            "plugin1",
            "tool_plugin_audit",
            {"target": PATH, "checks": ["filesystem", "permission"], "token": "PLUGIN-9001"},
        ),
        _tool(
            "plugin1",
            "tool_plugin_audit",
            json.dumps(
                {
                    "ok": False,
                    "error": "E_ADDR_IN_USE",
                    "path": PATH,
                    "plugin_token": "PLUGIN-9001",
                },
                ensure_ascii=False,
            ),
        ),
        _assistant_tool("skill1", "load_skill_tdd", {}),
        _tool("skill1", "load_skill_tdd", skill_body),
        _assistant_tool(
            "perm1",
            "write_file",
            {"path": r"E:\AgentSuper\backend\config.json", "content": "x"},
        ),
        _tool(
            "perm1",
            "write_file",
            "已拒绝执行工具 write_file（用户未授权）；request_id=perm-4242；"
            r"path=E:\AgentSuper\backend\config.json",
        ),
        {"role": "user", "content": "继续，保持权限状态不变。"},
        {"role": "assistant", "content": "已记录，等待下一步。"},
    ]


def test_tool_history_from_filesystem_plugin_skill_permission_serializes():
    messages = _mixed_history()
    serialized = serialize_messages(messages)

    for name in ("read_file", "tool_plugin_audit", "load_skill_tdd", "write_file"):
        assert name in serialized

    facts = extract_precision_values(serialized)
    assert PATH in facts
    assert any(COMMAND in fact for fact in facts)
    assert "864213" in facts
    assert "9001" in facts
    assert not any(fact.startswith("\\\\") for fact in facts)


def test_escaped_tool_args_do_not_duplicate_precision_backfill():
    messages = _mixed_history()
    summary = "\n".join(
        [
            f"文件 {PATH}",
            f"代码路径 `{PATH}`",
            "会话号 864213",
            "错误 E_ADDR_IN_USE",
            f"命令 `{COMMAND}`",
            "插件结果 PLUGIN-9001",
            "权限请求 perm-4242",
            r"权限路径 E:\AgentSuper\backend\config.json",
        ]
    )

    assert enforce_precision(summary, messages) == summary


class _FakeModel:
    def __init__(self, text: str):
        self.text = text

    async def ainvoke(self, messages):
        return SimpleNamespace(content=self.text)


@pytest.mark.asyncio
async def test_compress_mixed_tool_history_keeps_precision_and_permission_denial():
    messages = _mixed_history()
    summary = "\n".join(
        [
            f"文件 {PATH}",
            f"代码路径 `{PATH}`",
            "会话号 864213",
            "错误 E_ADDR_IN_USE",
            f"命令 `{COMMAND}`",
            "插件结果 PLUGIN-9001",
            "权限请求 perm-4242",
            r"权限路径 E:\AgentSuper\backend\config.json",
            "已拒绝执行工具 write_file（用户未授权）",
        ]
    )

    out = await compress_messages(
        messages,
        threshold=0,
        keep_recent=2,
        keep_tokens=0,
        require_precision=True,
        require_llm=True,
        model=_FakeModel(summary),
    )

    assert out.triggered is True
    assert out.missing_precision == []
    assert "write_file" in out.summary
    assert "load_skill_tdd" in serialize_messages(messages)
    assert "已拒绝执行工具 write_file（用户未授权）" in out.summary
