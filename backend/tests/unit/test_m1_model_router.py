"""L1 单元测试：模型路由（agent.md §2）。"""

from models.router import (
    DEFAULT_TIMEOUT,
    LOCAL_OLLAMA_MAX_TOKENS,
    LOCAL_OLLAMA_TIMEOUT,
    TIER_MAP,
    TierModel,
    _ensure_provider,
    get_model,
)


def test_tier_map_aliases():
    assert TIER_MAP["auto"] == "smart-router"
    assert TIER_MAP["complex"] == "tier-complex"
    assert TIER_MAP["reasoning"] == "tier-complex"


def test_get_model_returns_tier_model():
    m = get_model("simple")
    assert isinstance(m, TierModel)
    assert m.tier == "simple"


def test_unknown_tier_falls_back_to_auto():
    assert get_model("bogus").tier == "auto"


def test_ensure_provider_prefix():
    assert _ensure_provider("deepseek-chat", "https://api.deepseek.com") == "deepseek/deepseek-chat"
    assert _ensure_provider("gpt-4o", "https://api.openai.com/v1") == "openai/gpt-4o"
    assert _ensure_provider("deepseek/x", "https://api.deepseek.com") == "deepseek/x"


def test_endpoint_uses_proxy_when_configured(monkeypatch):
    monkeypatch.setenv("LITELLM_PROXY_URL", "http://localhost:4000")
    monkeypatch.setenv("LITELLM_PROXY_KEY", "sk-test")
    ep = get_model("medium")._endpoint()
    assert ep["model"] == "tier-medium"
    assert ep["api_base"] == "http://localhost:4000"


def test_endpoint_direct_without_proxy(monkeypatch):
    monkeypatch.delenv("LITELLM_PROXY_URL", raising=False)
    ep = get_model("medium")._endpoint()
    assert "/" in ep["model"]


def test_timeout_local_ollama_widened_by_default():
    m = TierModel()
    assert m.timeout == DEFAULT_TIMEOUT
    # 本地 Ollama（model 前缀 / api_base 本机）→ 放宽到 LOCAL_OLLAMA_TIMEOUT
    assert m._timeout_for({"model": "ollama/qwen2.5:3b"}) == LOCAL_OLLAMA_TIMEOUT
    assert (
        m._timeout_for({"model": "x", "api_base": "http://127.0.0.1:11434"})
        == LOCAL_OLLAMA_TIMEOUT
    )
    # 远程模型保持默认
    assert m._timeout_for({"model": "deepseek/deepseek-v4-flash"}) == DEFAULT_TIMEOUT


def test_timeout_custom_value_honored_for_local():
    m = TierModel(timeout=42)
    assert m._timeout_for({"model": "ollama/x"}) == 42


def test_max_tokens_local_ollama_capped():
    """本地 Ollama 输出上限收窄到 LOCAL_OLLAMA_MAX_TOKENS（取 min 不放大）。"""
    m = TierModel()  # 默认 settings.llm_max_tokens
    assert m._max_tokens_for({"model": "ollama/qwen2.5:3b"}) == min(
        m.max_tokens, LOCAL_OLLAMA_MAX_TOKENS
    )
    # 远程模型不受影响
    assert m._max_tokens_for({"model": "deepseek/deepseek-v4-flash"}) == m.max_tokens
    # 本地代理 tier 别名（model 无 ollama/ 前缀）不误伤
    assert (
        m._max_tokens_for({"model": "tier-simple", "api_base": "http://localhost:4000"})
        == m.max_tokens
    )
    # 调用方自定义更小的值不放大
    m2 = TierModel(max_tokens=1000)
    assert m2._max_tokens_for({"model": "ollama/x"}) == 1000


def test_parse_response_coerces_dict_arguments_to_str():
    """部分 provider 把 arguments 给成 dict → 统一归一化为 JSON 字符串。"""
    import json
    from types import SimpleNamespace

    from models.router import _parse_response

    resp = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="",
                    tool_calls=[
                        SimpleNamespace(
                            id="c1",
                            function=SimpleNamespace(
                                name="tool_ls", arguments={"path": "."}
                            ),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        usage=None,
        model="ollama/m",
    )
    r = _parse_response(resp, "ollama/m")
    assert len(r.tool_calls) == 1
    tc = r.tool_calls[0]
    assert isinstance(tc.arguments, str)
    assert json.loads(tc.arguments) == {"path": "."}
    assert r.finish_reason == "tool-calls"
