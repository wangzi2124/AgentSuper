"""L1 单元测试：模型路由（agent.md §2）。"""

from models.router import TIER_MAP, TierModel, _ensure_provider, get_model


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
