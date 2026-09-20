"""模型目录（app/models/catalog.py）单测。"""

import json
import os

import pytest

from app.models import catalog
from app.config import settings


def test_builtin_catalog_has_deepseek_default_flag():
    cat = catalog.build_catalog()
    ids = {e["id"]: e for e in cat}
    assert "deepseek/deepseek-v4-flash" in ids
    assert ids["deepseek/deepseek-v4-flash"]["default"] is True
    # 每个条目都有价格四件套与能力
    for e in cat:
        assert set(e["cost"].keys()) >= {"input_per_1m", "output_per_1m",
                                          "cache_read_per_1m", "cache_write_per_1m"}
        assert set(e["capabilities"].keys()) >= {"tool_use", "vision", "reasoning"}


def test_normalize_model():
    assert catalog.normalize_model("deepseek/deepseek-v4-flash") == "deepseek/deepseek-v4-flash"
    assert catalog.normalize_model("ollama/qwen2.5:7b") == "ollama/qwen2.5:7b"
    # 裸名不猜 provider，原样保留
    assert catalog.normalize_model("qwen2.5:7b") == "qwen2.5:7b"
    assert catalog.normalize_model({"id": "openai/gpt-4o"}) == "openai/gpt-4o"
    assert catalog.normalize_model({"provider": "ollama", "name": "mistral"}) == "ollama/mistral"
    assert catalog.normalize_model(None) is None
    assert catalog.normalize_model("", default="x/y") == "x/y"


def test_lookup_unknown_returns_none():
    assert catalog.lookup("nope/nope") is None


def test_resolve_cost_math():
    # gpt-4o: in 2.5/1M, out 10/1M, cache_read 1.25/1M
    c = catalog.resolve_cost(
        "openai/gpt-4o",
        input_tokens=1_000_000, output_tokens=100_000,
        cache_read=400_000, cache_write=0,
    )
    noncached = 600_000
    assert c == pytest.approx(
        noncached / 1e6 * 2.5 + 100_000 / 1e6 * 10.0 + 400_000 / 1e6 * 1.25
    )
    # 未知模型价格记 0
    assert catalog.resolve_cost("nope/nope", input_tokens=1000, output_tokens=1000) == 0.0


def test_extract_usage_cache_and_reasoning():
    class Det:
        cached_tokens = 200

    class CDet:
        reasoning_tokens = 30

    class Usage:
        prompt_tokens = 1000
        completion_tokens = 500
        prompt_tokens_details = Det()
        completion_tokens_details = CDet()
        prompt_cache_hit_tokens = 0
        prompt_cache_miss_tokens = 0

    u = catalog.extract_usage(Usage())
    assert u == {"input": 1000, "output": 500, "reasoning": 30,
                 "cache_read": 200, "cache_write": 800}
    # None 兜底
    assert catalog.extract_usage(None, pt=10, ct=5)["input"] == 10


def test_sum_usage_keywise():
    a = {"input": 1, "output": 2, "reasoning": 3, "cache_read": 4, "cache_write": 5}
    b = {"input": 10, "output": 20, "reasoning": 30, "cache_read": 40, "cache_write": 50}
    s = catalog.sum_usage(a, b)
    assert s == {k: a[k] + b[k] for k in a}
    # 缺键按 0
    assert catalog.sum_usage({"input": 1}, {"output": 2})["input"] == 1


def test_override_file(tmp_path, monkeypatch):
    # 用 tmp 作为 data 目录写入覆盖文件，避免污染真实 data/（强制 sqlite 隔离）
    monkeypatch.setenv("AGENTSUPER_DATA", str(tmp_path))
    monkeypatch.setattr("app.models.catalog_db.backends.is_sqlite", lambda: True)
    override = {
        "default_model": "ollama/my-custom:latest",
        "overrides": {
            "openai/gpt-4o": {"cost": {"input_per_1m": 0.01}},
        },
        "extra": [{
            "id": "ollama/my-custom:latest", "provider": "ollama", "family": "my-custom",
            "name": "My Custom", "description": "",
            "capabilities": {"tool_use": True, "vision": False, "reasoning": False},
            "cost": {"input_per_1m": 0.0, "output_per_1m": 0.0,
                     "cache_read_per_1m": 0.0, "cache_write_per_1m": 0.0},
        }],
    }
    (tmp_path / "model_catalog.json").write_text(json.dumps(override), encoding="utf-8")
    cat = catalog.build_catalog()
    by_id = {e["id"]: e for e in cat}
    assert "ollama/my-custom:latest" in by_id
    assert by_id["openai/gpt-4o"]["cost"]["input_per_1m"] == 0.01
    assert by_id["openai/gpt-4o"]["cost"]["output_per_1m"] == 10.0  # 未覆盖字段保留
    assert catalog.default_model() == "ollama/my-custom:latest"


def test_default_model_falls_back_to_settings():
    cat_flag = any(e.get("default") for e in catalog.build_catalog())
    assert cat_flag
    assert catalog.default_model()


def test_provider_config_hint_ollama_and_none_configured():
    # 本地模型 / 无模型 / 裸名（无 provider 前缀）一律视为已配置
    assert catalog.provider_config_hint("ollama/qwen2.5:3b") == ""
    assert catalog.provider_config_hint(None) == ""
    assert catalog.provider_config_hint("") == ""
    assert catalog.provider_config_hint("qwen2.5:7b", creds={"api_base": None, "api_key": None}) == ""


def test_provider_config_hint_missing_creds_raises_hint(monkeypatch):
    # 未注册 provider + 兜底指向 ollama 本地/默认 deepseek → 返回友好提示
    monkeypatch.setattr(catalog, "read_providers", lambda: {})
    for base in ("http://localhost:11434", "https://api.deepseek.com"):
        hint = catalog.provider_config_hint(
            "deepseek/deepseek-v4-flash",
            creds={"api_base": base, "api_key": "", "is_ollama": False},
        )
        assert "deepseek" in hint
        assert "模型管理" in hint or "LLM_API_BASE" in hint


def test_provider_config_hint_custom_base_or_key_configured(monkeypatch):
    # 用户显式配置了自定义 api_base（无 key 的本地服务）或有 api_key → 视为已配置
    monkeypatch.setattr(catalog, "read_providers", lambda: {})
    assert catalog.provider_config_hint(
        "vllm/qwen2.5", creds={"api_base": "http://127.0.0.1:8000/v1", "api_key": "", "is_ollama": False}
    ) == ""
    assert catalog.provider_config_hint(
        "openai/gpt-4o", creds={"api_base": None, "api_key": "sk-test", "is_ollama": False}
    ) == ""


def test_provider_config_hint_registered_provider_configured(monkeypatch):
    # 模型管理页已注册该 provider（带 api_base）→ 已配置
    monkeypatch.setattr(
        catalog, "read_providers",
        lambda: {"deepseek": {"label": "DeepSeek", "api_base": "https://api.deepseek.com",
                             "api_key": "sk-x", "enabled": True}},
    )
    assert catalog.provider_config_hint(
        "deepseek/deepseek-v4-flash",
        creds={"api_base": "https://api.deepseek.com", "api_key": "sk-x", "is_ollama": False},
    ) == ""


def test_ollama_model_installed(monkeypatch):
    # 非 ollama / 空模型 → 放行
    assert catalog.ollama_model_installed(None) is True
    assert catalog.ollama_model_installed("deepseek/deepseek-v4-flash") is True

    # 固定探测结果，避免依赖真实 ollama（测试机/CI 可能无 ollama 或模型不同）
    monkeypatch.setattr(catalog, "probe_ollama_models", lambda known, force=False: [])

    # 探测成功 + 模型已装 → True
    monkeypatch.setattr(catalog, "_ollama_probe_ok", True)
    monkeypatch.setattr(catalog, "_ollama_installed", {"qwen2.5:3b"})
    assert catalog._ollama_installed_names() == {"qwen2.5:3b"}
    assert catalog.ollama_model_installed("ollama/qwen2.5:3b") is True

    # 探测成功 + 模型未装 → False
    assert catalog.ollama_model_installed("ollama/qwen2.5:7b") is False

    # 探测失败（ollama 未启动）→ 放行，不误判
    monkeypatch.setattr(catalog, "_ollama_probe_ok", False)
    monkeypatch.setattr(catalog, "_ollama_installed", None)
    assert catalog.ollama_model_installed("ollama/qwen2.5:7b") is True


def test_provider_config_hint_ollama_model_not_installed(monkeypatch):
    monkeypatch.setattr(catalog, "probe_ollama_models", lambda known, force=False: [])
    monkeypatch.setattr(catalog, "_ollama_probe_ok", True)
    monkeypatch.setattr(catalog, "_ollama_installed", {"qwen2.5:3b"})
    hint = catalog.provider_config_hint("ollama/qwen2.5:7b")
    assert "qwen2.5:7b" in hint
    assert "ollama pull" in hint
    # 已安装的模型不提示
    assert catalog.provider_config_hint("ollama/qwen2.5:3b") == ""


def test_normalize_llm_exception_ollama_not_found(monkeypatch):
    class FakeConnectionError(Exception):
        pass

    monkeypatch.setattr(catalog, "probe_ollama_models", lambda known, force=False: [])
    monkeypatch.setattr(catalog, "_ollama_probe_ok", True)
    monkeypatch.setattr(catalog, "_ollama_installed", set())
    exc = FakeConnectionError(
        'OllamaException - {"error":"model \'qwen2.5:7b\' not found"}'
    )
    msg = catalog.normalize_llm_exception(exc, "ollama/qwen2.5:7b")
    assert "ollama pull qwen2.5:7b" in msg
    # 不带 model_id 时也能从文本解析
    msg2 = catalog.normalize_llm_exception(exc)
    assert "模型不存在" in msg2


def test_normalize_llm_exception_connection_and_auth():
    class FakeConn(Exception):
        pass

    assert "连接" in catalog.normalize_llm_exception(FakeConn("connection refused: 127.0.0.1:11434"))
    class FakeAuth(Exception):
        pass

    assert "鉴权" in catalog.normalize_llm_exception(FakeAuth("401 Unauthorized: invalid api key"))
    assert catalog.normalize_llm_exception(FakeConn("unrelated format issue")) == ""


def test_friendly_chat_error_priority():
    from app.session.agent_executor import friendly_chat_error
    # 已带友好中文的 RuntimeError → 原样透传
    assert friendly_chat_error(RuntimeError("本地 Ollama 未安装模型「qwen2.5:7b」。")) == \
        "本地 Ollama 未安装模型「qwen2.5:7b」。"
    # 可被 normalize 识别的其它异常 → 中文
    class FakeConn(Exception):
        pass

    assert "ollama" in friendly_chat_error(
        FakeConn('OllamaException - {"error":"model \'x:y\' not found"}'),
        model="ollama/x:y",
    )
    # 无法识别 → 兜底通用文案
    assert friendly_chat_error(FakeConn("some cryptic failure"), model="deepseek/deepseek-v4-flash") == \
        "处理请求时发生内部错误，请稍后重试"


def _fake_ollama_tags(tmp_path, monkeypatch, models):
    """写入假 /api/tags 响应并让 probe_ollama_models 从文件读，避免依赖真实 ollama。"""
    import urllib.request

    payload = json.dumps({"models": models}).encode("utf-8")
    tags_file = tmp_path / "tags.json"
    tags_file.write_bytes(payload)

    real_urlopen = urllib.request.urlopen
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda req, timeout: _FakeResp(tags_file.read_bytes()) if "api/tags" in req.full_url else real_urlopen(req, timeout=timeout),
    )
    # 重置全局探测缓存，强制重新探测
    monkeypatch.setattr(catalog, "_ollama_cache", None)
    monkeypatch.setattr(catalog, "_ollama_cache_time", 0)
    monkeypatch.setattr(catalog, "_ollama_probe_ok", False)


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_probe_ollama_models_maps_capabilities(tmp_path, monkeypatch):
    # 只保留「目录未收录」的增量：已收录模型（ollama/qwen2.5:3b）被跳过
    _fake_ollama_tags(tmp_path, monkeypatch, [
        {"name": "qwen3.5:4b", "capabilities": ["vision", "completion", "tools", "thinking"]},
        {"name": "llava:latest", "capabilities": ["completion", "vision"]},
        {"name": "qwen2.5:3b", "capabilities": ["completion", "tools"]},
        {"name": "mistral:latest", "capabilities": ["completion", "tools"]},
    ])
    extras = catalog.probe_ollama_models(known_ids={"ollama/qwen2.5:3b"}, force=True)
    by_id = {e["id"]: e for e in extras}
    assert set(by_id) == {"ollama/qwen3.5:4b", "ollama/llava:latest", "ollama/mistral:latest"}
    # capabilities 映射：tools→tool_use、vision→vision、thinking→reasoning
    assert by_id["ollama/qwen3.5:4b"]["capabilities"] == \
        {"tool_use": True, "vision": True, "reasoning": True}
    assert by_id["ollama/llava:latest"]["capabilities"] == \
        {"tool_use": False, "vision": True, "reasoning": False}
    assert by_id["ollama/mistral:latest"]["capabilities"] == \
        {"tool_use": True, "vision": False, "reasoning": False}
    # 探测成功 → 已安装名集合含全部模型（含已收录的）
    assert catalog._ollama_installed_names() == {
        "qwen3.5:4b", "llava:latest", "qwen2.5:3b", "mistral:latest"}


def test_read_capabilities_returns_declared_and_unknown(monkeypatch):
    # 内置目录 deepseek → 按目录能力返回
    caps = catalog.read_capabilities("deepseek/deepseek-v4-flash")
    assert set(caps) == {"tool_use", "vision", "reasoning"}
    assert all(isinstance(v, bool) for v in caps.values())
    # 未收录模型 → {}（调用方按默认处理，不臆断）
    assert catalog.read_capabilities("nope/nope") == {}
    assert catalog.read_capabilities("") == {}
    assert catalog.read_capabilities(None) == {}


def test_read_capabilities_ollama_extra_respects_override(tmp_path, monkeypatch):
    # 目录 extra 区显式声明的能力优先——读得回 DB/覆盖文件保存的勾选
    monkeypatch.setenv("AGENTSUPER_DATA", str(tmp_path))
    monkeypatch.setattr("app.models.catalog_db.backends.is_sqlite", lambda: True)
    override = {
        "extra": [{
            "id": "ollama/qwen3.5:4b", "provider": "ollama", "family": "qwen3.5",
            "name": "Qwen3.5 4B", "description": "",
            "capabilities": {"tool_use": True, "vision": False, "reasoning": True},
            "cost": {"input_per_1m": 0.0, "output_per_1m": 0.0,
                     "cache_read_per_1m": 0.0, "cache_write_per_1m": 0.0},
        }],
    }
    (tmp_path / "model_catalog.json").write_text(json.dumps(override), encoding="utf-8")
    caps = catalog.read_capabilities("ollama/qwen3.5:4b")
    assert caps == {"tool_use": True, "vision": False, "reasoning": True}


def test_provider_api_ollama_think_from_reasoning_capability(monkeypatch):
    # ollama + 声明推理 → think=True；未声明推理 → 不传 think（不臆断）
    monkeypatch.setattr(catalog, "read_capabilities", lambda m: {"tool_use": True, "vision": False, "reasoning": True})
    creds = catalog.provider_api("ollama/qwen3.5:4b")
    assert creds["is_ollama"] is True
    assert creds["options"]["think"] is True
    assert creds["options"]["num_ctx"] == settings.ollama_num_ctx

    monkeypatch.setattr(catalog, "read_capabilities", lambda m: {"tool_use": True, "vision": True, "reasoning": False})
    assert catalog.provider_api("ollama/llava:latest")["options"]["think"] is False

    # 未收录/能力缺失 → options 不含 think
    monkeypatch.setattr(catalog, "read_capabilities", lambda m: {})
    opts = catalog.provider_api("ollama/something-new")["options"]
    assert "think" not in opts

    # 非 ollama（deepseek）→ options=None，绝不含 think
    monkeypatch.setattr(catalog, "read_providers", lambda: {})
    creds = catalog.provider_api("deepseek/deepseek-v4-flash")
    assert creds["is_ollama"] is False
    assert creds["options"] is None


def test_litellm_extra_kwargs_expands_think_and_num_ctx():
    # 展开 ollama options → 顶层 kwargs（供 ** 注入 litellm 调用点）
    kw = catalog.litellm_extra_kwargs({"options": {"num_ctx": 8192, "think": True}})
    assert kw == {"num_ctx": 8192, "think": True}
    kw2 = catalog.litellm_extra_kwargs({"options": {"think": False}})
    assert kw2 == {"think": False}
    # 非 ollama（options=None）→ {}，不污染其它 provider
    assert catalog.litellm_extra_kwargs({"options": None}) == {}
    assert catalog.litellm_extra_kwargs(None) == {}
    # think 透传时不传 num_ctx=None
    kw3 = catalog.litellm_extra_kwargs({"options": {"num_ctx": None, "think": True}})
    assert kw3 == {"think": True}