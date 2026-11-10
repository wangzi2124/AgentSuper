"""Unit tests for the ModelRuntime seam (Turn Scope, warmth, sticky num_ctx)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from config import settings
from models import catalog, runtime
from models.context_guard import FitResult
from models.router import TierModel
from models.runtime import ModelRuntime, RuntimeSpec


@pytest.fixture(autouse=True)
def _reset():
    runtime.reset_state()
    runtime.reset_scope()
    catalog._invalidate_config_cache()
    yield
    runtime.reset_state()
    runtime.reset_scope()
    catalog._invalidate_config_cache()


def _response(content: str = "ok"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=[]),
                finish_reason="stop",
            )
        ],
        usage=None,
        model="ollama_chat/x",
    )


def test_turn_scope_reuses_runtime(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(settings, "litellm_proxy_url", "")
    monkeypatch.setattr(settings, "llm_model", "deepseek/deepseek-v4-flash")
    monkeypatch.setattr(
        catalog,
        "provider_api",
        lambda mid: calls.append(mid)
        or {"api_base": None, "api_key": "k", "is_ollama": False},
    )
    spec = RuntimeSpec(tier="auto")
    token = runtime.begin_scope()
    try:
        first = runtime.resolve_runtime(spec)
        second = runtime.resolve_runtime(spec)
        assert first is second
        assert first.model_name() == second.model_name()
        assert len(calls) == 1
    finally:
        runtime.end_scope(token)


def test_without_scope_each_call_resolves(monkeypatch):
    monkeypatch.setattr(settings, "litellm_proxy_url", "")
    monkeypatch.setattr(settings, "llm_model", "deepseek/deepseek-v4-flash")
    spec = RuntimeSpec(tier="auto")
    assert runtime.resolve_runtime(spec) is not runtime.resolve_runtime(spec)


def test_endpoint_carries_provider_timeout(monkeypatch):
    """provider_api 返回的 timeout 必须进入 endpoint 并被 _timeout_for 消费（表单参数参与运行）。"""
    monkeypatch.setattr(settings, "litellm_proxy_url", "")
    monkeypatch.setattr(settings, "llm_model", "mycloud/m1")
    monkeypatch.setattr(
        catalog,
        "provider_api",
        lambda mid: {
            "api_base": "http://127.0.0.1:8001/v1",
            "api_key": "k",
            "is_ollama": False,
            "timeout": 90,
        },
    )
    rt = ModelRuntime(RuntimeSpec(model_override="mycloud/m1"))
    ep = rt.endpoint()
    assert ep["timeout"] == 90
    assert rt._timeout_for(ep) == 90


def test_endpoint_timeout_absent_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "litellm_proxy_url", "")
    monkeypatch.setattr(
        catalog,
        "provider_api",
        lambda mid: {"api_base": "https://api.example.com/v1", "api_key": "k", "is_ollama": False},
    )
    rt = ModelRuntime(RuntimeSpec(model_override="mycloud/m1", timeout=120))
    ep = rt.endpoint()
    assert not ep.get("timeout")
    assert rt._timeout_for(ep) == 120  # 未配置时回退 RuntimeSpec 默认超时


def test_registered_ollama_endpoint_locality_by_api_base(monkeypatch):
    """显式注册的 Ollama Provider（模型 id 可能带 ollama/ 前缀）：本地性按
    api_base 判定，不再被 id 前缀短路到本地适配器。"""
    monkeypatch.setattr(settings, "litellm_proxy_url", "")
    monkeypatch.setattr(
        catalog,
        "provider_api",
        lambda mid: {
            "api_base": "https://ollama.com",
            "api_key": "cloud-key",
            "is_ollama": True,
            "litellm_model": "ollama_chat/beam",
            "ollama_registered": True,
        },
    )
    rt = ModelRuntime(RuntimeSpec(model_override="ollama/beam"))
    ep = rt.endpoint()
    assert ep["is_ollama"] is True
    assert ep["ollama_registered"] is True
    # 云端地址 + ollama/ 前缀 -> 不是本地端点 -> 走 Cloud 适配器（无 warm/num_ctx）
    assert runtime._is_local_ollama_endpoint(ep) is False
    assert runtime._adapter_for(ep).name == "cloud"

    # 同一注册表 Provider 指向 localhost -> 本地端点
    monkeypatch.setattr(
        catalog,
        "provider_api",
        lambda mid: {
            "api_base": "http://localhost:11434",
            "api_key": "",
            "is_ollama": True,
            "ollama_registered": True,
        },
    )
    rt_local = ModelRuntime(RuntimeSpec(model_override="ollama/qwen:7b"))
    ep_local = rt_local.endpoint()
    assert runtime._is_local_ollama_endpoint(ep_local) is True


def test_unregistered_ollama_prefix_still_local(monkeypatch):
    """无注册表的本地 Ollama 模型（ollama_chat/ 前缀）：保持原 id 前缀判本地。"""
    monkeypatch.setattr(settings, "litellm_proxy_url", "")
    monkeypatch.setattr(
        catalog,
        "provider_api",
        lambda mid: {
            "api_base": "http://localhost:11434",
            "api_key": "ollama",
            "is_ollama": True,
            "litellm_model": "ollama_chat/qwen:7b",
        },
    )
    rt = ModelRuntime(RuntimeSpec(model_override="ollama_chat/qwen:7b"))
    ep = rt.endpoint()
    assert not ep.get("ollama_registered")
    assert runtime._is_local_ollama_endpoint(ep) is True
    assert runtime._adapter_for(ep).name == "ollama"


def test_local_ollama_transport_sends_keep_alive(monkeypatch):
    monkeypatch.setattr(settings, "litellm_proxy_url", "")
    monkeypatch.setattr(settings, "ollama_context_guard", False)
    monkeypatch.setattr(settings, "ollama_keep_alive", "30m")
    monkeypatch.setattr(runtime, "effective_window", lambda mid, fallback=0: 4096)
    monkeypatch.setattr(catalog, "_explicit_context_override", lambda mid: 0)
    seen: dict = {}

    def fake_completion(**kwargs):
        seen.update(kwargs)
        return _response()

    monkeypatch.setattr(runtime.litellm, "completion", fake_completion)
    model = TierModel(tier="auto")
    monkeypatch.setattr(
        model,
        "_endpoint",
        lambda: {
            "model": "ollama_chat/x",
            "api_base": "http://127.0.0.1:11434",
            "api_key": "ollama",
        },
    )
    model.raw_invoke([{"role": "user", "content": "hi"}])
    assert seen["keep_alive"] == "30m"
    assert seen["timeout"] == runtime.LOCAL_OLLAMA_TIMEOUT


def test_num_ctx_sticks_after_expansion(monkeypatch):
    monkeypatch.setattr(catalog, "_explicit_context_override", lambda mid: 0)
    rt = ModelRuntime(RuntimeSpec())
    endpoint = {"model": "ollama_chat/x", "api_base": "http://127.0.0.1:11434"}
    expanded = FitResult(model="ollama_chat/x", messages=[], window=8192, num_ctx=16384)
    assert rt._num_ctx_for(endpoint, expanded) == 16384
    assert runtime.sticky_num_ctx("ollama_chat/x") == 16384
    normal = FitResult(model="ollama_chat/x", messages=[], window=8192, num_ctx=0)
    assert rt._num_ctx_for(endpoint, normal) == 16384


def test_explicit_override_wins_over_sticky(monkeypatch):
    monkeypatch.setattr(catalog, "_explicit_context_override", lambda mid: 4096)
    runtime._remember_num_ctx("ollama_chat/x", 16384)
    rt = ModelRuntime(RuntimeSpec())
    endpoint = {"model": "ollama_chat/x", "api_base": "http://127.0.0.1:11434"}
    normal = FitResult(model="ollama_chat/x", messages=[], window=4096, num_ctx=0)
    assert rt._num_ctx_for(endpoint, normal) == 0


def test_warm_posts_keep_alive(monkeypatch):
    monkeypatch.setattr(settings, "ollama_keep_alive", "30m")
    captured: dict = {}

    def fake_warm(base, payload):
        captured["base"] = base
        captured["payload"] = payload

    monkeypatch.setattr(runtime, "_warm_sync", fake_warm)
    rt = ModelRuntime(RuntimeSpec(model_override="ollama_chat/x"))
    monkeypatch.setattr(
        rt,
        "_endpoint",
        lambda: {
            "model": "ollama_chat/x",
            "api_base": "http://127.0.0.1:11434",
            "api_key": "ollama",
        },
    )
    assert asyncio.run(rt.warm()) is True
    assert captured["base"] == "http://127.0.0.1:11434"
    assert captured["payload"]["model"] == "x"
    assert captured["payload"]["keep_alive"] == "30m"


def test_deepseek_image_uses_openai_compatible_route(monkeypatch):
    image = {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,QUJD"},
    }
    image_messages = [
        {"role": "user", "content": [{"type": "text", "text": "看图"}, image]}
    ]
    text_messages = [{"role": "user", "content": "hello"}]
    assert (
        runtime._transport_model(
            {"model": "deepseek/deepseek-v4-flash"}, image_messages
        )
        == "openai/deepseek-v4-flash"
    )
    assert (
        runtime._transport_model(
            {"model": "deepseek/deepseek-v4-flash"}, text_messages
        )
        == "deepseek/deepseek-v4-flash"
    )
    assert (
        runtime._transport_model(
            {"model": "ollama_chat/llava:latest"}, image_messages
        )
        == "ollama_chat/llava:latest"
    )

    monkeypatch.setattr(settings, "litellm_proxy_url", "")
    monkeypatch.setattr(catalog, "_explicit_context_override", lambda mid: 0)
    model = TierModel(tier="auto")
    monkeypatch.setattr(
        model,
        "_endpoint",
        lambda: {
            "model": "deepseek/deepseek-v4-flash",
            "api_base": "https://api.deepseek.com",
            "api_key": "k",
            "is_ollama": False,
        },
    )
    kwargs, _, _ = model._transport_kwargs(image_messages, None)
    assert kwargs["model"] == "openai/deepseek-v4-flash"


def test_numeric_keep_alive_is_sent_as_int(monkeypatch):
    endpoint = {"model": "ollama_chat/x", "is_ollama": True}
    for raw, expected in (("-1", -1), ("0", 0), ("30m", "30m")):
        monkeypatch.setattr(settings, "ollama_keep_alive", raw)
        kwargs: dict = {}
        runtime._OLLAMA_ADAPTER.augment_kwargs(kwargs, endpoint)
        assert kwargs["keep_alive"] == expected


def test_warm_payload_normalizes_numeric_keep_alive(monkeypatch):
    monkeypatch.setattr(settings, "ollama_keep_alive", "-1")
    captured: dict = {}
    monkeypatch.setattr(
        runtime, "_warm_sync", lambda base, payload: captured.update(payload=payload)
    )
    rt = ModelRuntime(RuntimeSpec(model_override="ollama_chat/x"))
    monkeypatch.setattr(
        rt,
        "_endpoint",
        lambda: {
            "model": "ollama_chat/x",
            "api_base": "http://127.0.0.1:11434",
            "api_key": "ollama",
            "is_ollama": True,
        },
    )
    assert asyncio.run(rt.warm()) is True
    assert captured["payload"]["keep_alive"] == -1


def test_local_non_ollama_endpoint_gets_no_keep_alive(monkeypatch):
    monkeypatch.setattr(settings, "litellm_proxy_url", "")
    monkeypatch.setattr(settings, "ollama_context_guard", False)
    monkeypatch.setattr(catalog, "_explicit_context_override", lambda mid: 0)
    model = TierModel(tier="auto")
    monkeypatch.setattr(
        model,
        "_endpoint",
        lambda: {
            "model": "vllm/qwen",
            "api_base": "http://127.0.0.1:8001",
            "api_key": "k",
            "is_ollama": False,
        },
    )
    kwargs, _, _ = model._transport_kwargs([{"role": "user", "content": "hi"}], None)
    assert "keep_alive" not in kwargs
    assert "num_ctx" not in kwargs


def test_local_non_ollama_warm_is_noop(monkeypatch):
    called: list = []
    monkeypatch.setattr(runtime, "_warm_sync", lambda *args: called.append(args))
    rt = ModelRuntime(RuntimeSpec())
    monkeypatch.setattr(
        rt,
        "_endpoint",
        lambda: {
            "model": "vllm/qwen",
            "api_base": "http://127.0.0.1:8001",
            "api_key": "k",
            "is_ollama": False,
        },
    )
    assert asyncio.run(rt.warm()) is False
    assert called == []


def test_provider_adapter_selection_is_explicit():
    from models.runtime import _CLOUD_ADAPTER, _OLLAMA_ADAPTER, _adapter_for

    assert (
        _adapter_for({"model": "ollama_chat/x", "api_base": None})
        is _OLLAMA_ADAPTER
    )
    assert _adapter_for({"model": "deepseek/deepseek-v4-flash"}) is _CLOUD_ADAPTER
    assert (
        _adapter_for({"model": "ollama-cloud/gemma", "api_base": "https://remote"})
        is _CLOUD_ADAPTER
    )
    assert _CLOUD_ADAPTER.supports_overflow_retry({"model": "deepseek/x"}) is False
    assert asyncio.run(_CLOUD_ADAPTER.warm({"model": "deepseek/x"})) is False


def test_config_reads_are_cached_and_invalidated(monkeypatch):
    from models import catalog_db

    calls = {"n": 0}
    original = catalog_db.load_config

    def counted():
        calls["n"] += 1
        return original()

    monkeypatch.setattr(catalog_db, "load_config", counted)
    catalog._invalidate_config_cache()
    first = catalog.load_config()
    second = catalog.load_config()
    assert calls["n"] == 1
    assert first == second
    catalog._invalidate_config_cache()
    catalog.load_config()
    assert calls["n"] == 2
