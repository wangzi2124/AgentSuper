"""L1 单元测试：Ollama 兼容（对齐 ollama.txt 规格）。

覆盖：
- 前缀识别（ollama/ 与 ollama_chat/ 双识别，ollama_chat/ 为 LiteLLM 推荐前缀）；
- 模型名提取（去掉任一前缀）；
- Ollama 工具轮次流式优先（方言归一化兜底，失败回退非流式）；
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
async def test_ollama_tool_round_streams_first(monkeypatch):
    """带 tools 的 Ollama 调用走流式优先（方言归一化兜底），失败才回退非流式。"""
    import models.stream as stream_mod

    called: dict[str, object] = {"stream": False, "nonstream": False}

    class FakeStream:
        def __aiter__(self):
            async def gen():
                yield SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            finish_reason="stop",
                            delta=SimpleNamespace(
                                content="ok", reasoning_content=None, tool_calls=None
                            ),
                        )
                    ],
                )
            return gen()

    async def fake_acompletion(**kwargs):
        if kwargs.get("stream"):
            called["stream"] = True
            return FakeStream()
        called["nonstream"] = True
        raise AssertionError("流式成功时不应回退非流式")

    monkeypatch.setattr(stream_mod.litellm, "acompletion", fake_acompletion)

    result = await stream_mod.stream_chat(
        [{"role": "user", "content": "hi"}],
        model="ollama_chat/qwen2.5:3b",
        tools=[{"type": "function", "function": {"name": "tool_ls"}}],
    )
    assert called["stream"] is True
    assert called["nonstream"] is False
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




def test_ollama_type_provider_api_maps_to_native_chat(monkeypatch):
    monkeypatch.setattr(
        catalog,
        "read_providers",
        lambda: {"ollama-cloud": {"type": "ollama", "api_base": "https://ollama.com/v1", "api_key": "k"}},
    )
    creds = catalog.provider_api("ollama-cloud/gemma4:31b")
    assert creds["api_base"] == "https://ollama.com"
    assert creds["api_key"] == "k"
    assert creds["is_ollama"] is True
    assert creds["litellm_model"] == "ollama_chat/gemma4:31b"


def test_ollama_type_provider_config_hint_requires_key_for_cloud(monkeypatch):
    monkeypatch.setattr(
        catalog,
        "read_providers",
        lambda: {"ollama-cloud": {"type": "ollama", "api_base": "https://ollama.com", "api_key": ""}},
    )
    hint = catalog.provider_config_hint("ollama-cloud/gemma4:31b")
    assert "API Key" in hint


def test_ollama_tags_url_accepts_root_v1_and_api():
    assert catalog._ollama_tags_url("https://ollama.com") == "https://ollama.com/api/tags"
    assert catalog._ollama_tags_url("https://ollama.com/v1") == "https://ollama.com/api/tags"
    assert catalog._ollama_tags_url("https://ollama.com/api") == "https://ollama.com/api/tags"
    assert catalog._ollama_tags_url("http://localhost:11434/api/chat") == "http://localhost:11434/api/tags"


def test_upsert_provider_persists_type(monkeypatch):
    cfg = {"providers": {}}
    monkeypatch.setattr(catalog, "load_config", lambda: cfg)
    monkeypatch.setattr(catalog, "save_config", lambda c: cfg.update(c))
    monkeypatch.setattr(catalog, "reload_catalog", lambda: None)
    entry = catalog.upsert_provider(
        "ollama-cloud", {"type": "ollama", "api_base": "https://ollama.com", "api_key": "k"}
    )
    assert entry["type"] == "ollama"
    assert cfg["providers"]["ollama-cloud"]["type"] == "ollama"


def test_upsert_provider_rejects_reserved_ollama_prefix(monkeypatch):
    monkeypatch.setattr(catalog, "load_config", lambda: {"providers": {}})
    monkeypatch.setattr(catalog, "save_config", lambda c: None)
    monkeypatch.setattr(catalog, "reload_catalog", lambda: None)
    with pytest.raises(ValueError):
        catalog.upsert_provider("ollama_chat", {"type": "ollama", "api_base": "https://ollama.com"})


def test_probe_ollama_type_provider_uses_tags_with_bearer(monkeypatch):
    import json as _json
    import urllib.request as _urlreq

    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return _json.dumps(
                {
                    "models": [
                        {
                            "name": "gemma4:31b",
                            "details": {"context_length": 131072},
                            "capabilities": ["completion", "tools"],
                        }
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization") or ""
        return _Resp()

    monkeypatch.setattr(catalog, "local_endpoint_alive", lambda _base: True)
    monkeypatch.setattr(_urlreq, "urlopen", fake_urlopen)
    catalog._probe_cache.pop("ollama:cloud-test", None)

    extras = catalog.probe_ollama_provider_models(
        "cloud-test", "https://ollama.com", "secret", set(), force=True
    )

    assert seen["url"] == "https://ollama.com/api/tags"
    assert seen["auth"] == "Bearer secret"
    assert extras[0]["id"] == "cloud-test/gemma4:31b"
    assert extras[0]["context_length"] == 131072
    assert extras[0]["capabilities"]["tool_use"] is True