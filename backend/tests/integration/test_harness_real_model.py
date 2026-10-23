"""真实模型验收（agent-4 §7）：需显式开启，避免 CI 联网/计费。

    $env:HARNESS_REAL_TEST='1'
    $env:HARNESS_REAL_MODEL='deepseek/deepseek-v4-flash'   # 可选
    pytest tests/integration/test_harness_real_model.py -q -s
"""

from __future__ import annotations

import os

import pytest

from config import settings
from execution.tool_registry import ToolRegistry
from graph.nodes.base import run_tool_loop
from harness.delimiters import NAME_OPEN
from harness.tool_adapter import ToolAdapter
from models.router import TierModel

_REAL_ENABLED = str(
    os.environ.get("HARNESS_REAL_TEST")
    or getattr(settings, "harness_real_test", "")
    or ""
).strip().lower() in {"1", "true", "yes"}

pytestmark = pytest.mark.skipif(
    not _REAL_ENABLED,
    reason="真实模型测试默认跳过（设置 HARNESS_REAL_TEST=1 开启）",
)

MODEL = (
    os.environ.get("HARNESS_REAL_MODEL")
    or str(getattr(settings, "harness_real_model", "") or "")
    or "deepseek/deepseek-v4-flash"
)
TOKEN = "REAL-HARNESS-7788"


def _skip_without_key():
    if not settings.llm_api_key:
        pytest.skip("LLM_API_KEY 未配置")


def _registry():
    reg = ToolRegistry()

    def tool_echo(text: str) -> str:
        """原样回显文本。"""
        return f"ECHO:{text}"

    reg.register_function(
        tool_echo,
        name="tool_echo",
        description="回显给定文本",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )
    return reg


@pytest.mark.asyncio
async def test_real_native_tool_call(monkeypatch):
    _skip_without_key()
    monkeypatch.delenv("HARNESS_TOOL_MODE", raising=False)
    model = TierModel(tier="auto", timeout=120)
    monkeypatch.setattr(model, "_direct_model", lambda: MODEL)
    result = await ToolAdapter(model).complete(
        [
            {
                "role": "system",
                "content": "You must call tool_echo with text=REAL-HARNESS-7788.",
            },
            {"role": "user", "content": "Call the tool now."},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "tool_echo",
                    "description": "回显给定文本",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                },
            }
        ],
    )
    assert result.tool_calls, f"模型未返回原生 tool_calls：{result.content!r}"
    assert result.tool_calls[0].name == "tool_echo"
    assert TOKEN in result.tool_calls[0].arguments


@pytest.mark.asyncio
async def test_real_fallback_tool_loop(monkeypatch):
    """强制文本回退：验证提示词注入 → 定界符解析 → 执行 → 回灌 → 最终回答。"""
    _skip_without_key()
    monkeypatch.setenv("HARNESS_TOOL_MODE", "fallback")
    model = TierModel(tier="auto", timeout=120)
    monkeypatch.setattr(model, "_direct_model", lambda: MODEL)

    out = await run_tool_loop(
        model,
        [
            {
                "role": "system",
                "content": (
                    "你是测试助手。必须调用 tool_echo 一次，参数 text 使用 "
                    f"'{TOKEN}'，然后根据工具结果回答。"
                ),
            },
            {"role": "user", "content": "开始测试。"},
        ],
        _registry(),
        max_rounds=4,
        agent_id="harness-real",
    )

    assert out["tool_trace"], f"回退路径未触发工具调用：{out['text']!r}"
    assert out["tool_trace"][0]["name"] == "tool_echo"
    assert f"ECHO:{TOKEN}" in out["tool_trace"][0]["output"]
    assert TOKEN in out["text"] or "ECHO:" in out["text"]
    assert NAME_OPEN not in out["text"]
