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
    # 用 tmp 作为 data 目录写入覆盖文件，避免污染真实 data/
    monkeypatch.setenv("AGENTSUPER_DATA", str(tmp_path))
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