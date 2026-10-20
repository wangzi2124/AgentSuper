"""L1 单元测试：Ollama 兼容（对齐 ollama.txt 规格）。

覆盖：
- 前缀识别（ollama/ 与 ollama_chat/ 双识别，ollama_chat/ 为 LiteLLM 推荐前缀）；
- 模型名提取（去掉任一前缀）；
- Ollama 工具轮次强制非流式（避免 LiteLLM 流式工具调用缺陷）；
- 内置目录使用 ollama_chat/ 前缀。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from models import catalog
from models.catalog import (
    get_ollama_model_name,
    is_ollama_model,
    ollama_model_installed,
)
from models.builtin_catalog import BUILTIN_CATALOG


def test_is_ollama_model_both_prefixes():
    assert is_ollama_model("ollama/qwen2.5:3b")
    assert is_ollama_model("ollama_chat/qwen2.5:3b")
    assert not is_ollama_model("deepseek/deepseek-v4-flash")
    assert not is_ollama_model(None)


def test_get_ollama_model_name_strips_either_prefix():
    assert get_ollama_model_name("ollama/qwen2.5:3b") == "qwen2.5:3b"
    assert get_ollama_model_name("ollama_chat/qwen2.5:3b") == "qwen2.5:3b"
    assert get_ollama_model_name("deepseek/x") == "deepseek/x"


def test_builtin_catalog_uses_ollama_chat_prefix():
    """内置 Ollama 条目应统一使用 ollama_chat/ 前缀（规格推荐）。"""
    ollama_ids = [e["id"] for e in BUILTIN_CATALOG if e.get("provider") == "ollama"]
    assert ollama_ids, "应有内置 Ollama 条目"
    assert all(i.startswith("ollama_chat/") for i in ollama_ids), ollama_ids
    assert not any(i.startswith("ollama/") for i in ollama_ids), ollama_ids


def test_ollama_model_installed_accepts_ollama_chat(monkeypatch):
    """ollama_chat/<name> 也能按 /api/tags 判断是否安装（探测失败 → 放行）。"""
    # 探测失败（返回 None）→ 放行
    monkeypatch.setattr(catalog, "_ollama_installed_names", lambda: None)
    assert ollama_model_installed("ollama_chat/whatever") is True
    assert ollama_model_installed("ollama/whatever") is True
    assert ollama_model_installed("deepseek/x") is True
    assert ollama_model_installed(None) is True

    # 探测成功 + 空集 → 未安装
    monkeypatch.setattr(catalog, "_ollama_installed_names", lambda: set())
    assert ollama_model_installed("ollama_chat/missing") is False
    assert ollama_model_installed("ollama/missing") is False

    # 探测成功 + 命中 → 已安装
    monkeypatch.setattr(catalog, "_ollama_installed_names", lambda: {"qwen2.5:3b"})
    assert ollama_model_installed("ollama_chat/qwen2.5:3b") is True
    assert ollama_model_installed("ollama/qwen2.5:3b") is True


@pytest.mark.asyncio
async def test_ollama_tool_round_forces_nonstream(monkeypatch):
    """带 tools 的 Ollama 调用应走非流式（invoke）路径，避免流式工具调用缺陷。"""
    import models.stream as stream_mod

    called: dict[str, object] = {"stream": False, "nonstream": False}

    async def fake_acompletion(**kwargs):
        if kwargs.get("stream"):
            called["stream"] = True
            raise AssertionError("Ollama 工具轮次不应走流式")
        called["nonstream"] = True
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
            model=kwargs.get("model"),
        )

    monkeypatch.setattr(stream_mod.litellm, "acompletion", fake_acompletion)

    result = await stream_mod.stream_chat(
        [{"role": "user", "content": "hi"}],
        model="ollama_chat/qwen2.5:3b",
        tools=[{"type": "function", "function": {"name": "tool_ls"}}],
    )
    assert called["nonstream"] is True
    assert called["stream"] is False
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_non_ollama_tool_round_uses_stream(monkeypatch):
    """非 Ollama 带 tools 仍走流式路径，不受影响。"""
    import models.stream as stream_mod

    called: dict[str, object] = {"stream": False}

    class FakeStream:
        def __aiter__(self):
            async def gen():
                yield SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            finish_reason="stop",
                            delta=SimpleNamespace(content="hi", reasoning_content=None, tool_calls=None),
                        )
                    ],
                )
            return gen()

    async def fake_acompletion(**kwargs):
        if kwargs.get("stream"):
            called["stream"] = True
            return FakeStream()
        raise AssertionError("非 Ollama 不应走非流式优先")

    monkeypatch.setattr(stream_mod.litellm, "acompletion", fake_acompletion)
    result = await stream_mod.stream_chat(
        [{"role": "user", "content": "hi"}],
        model="deepseek/deepseek-v4-flash",
        tools=[{"type": "function", "function": {"name": "tool_ls"}}],
    )
    assert called["stream"] is True
    assert result.content == "hi"
