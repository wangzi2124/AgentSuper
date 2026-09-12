"""模型目录数据库层（app/models/catalog_db.py）单测。

用 AGENTSUPER_DATA 指向 tmp 目录隔离真实 data/；每次新路径独立初始化（建表+导 JSON）。
"""

import json

import pytest

from app.models import catalog, catalog_db


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTSUPER_DATA", str(tmp_path))
    return tmp_path


@pytest.fixture
def no_probes(monkeypatch):
    """打桩探测函数，删除路径的 reload_catalog 不做真实网络请求。"""
    monkeypatch.setattr("app.models.catalog.probe_ollama_models", lambda known, force=False: [])
    monkeypatch.setattr("app.models.catalog.probe_configured_providers", lambda known, force=False: [])


def test_empty_db_returns_empty(iso):
    assert catalog_db.load_config() == {}


def test_json_auto_import_keeps_backup(iso):
    data = {
        "default_model": "ollama/my:latest",
        "providers": {"vllm": {"label": "V", "api_base": "http://127.0.0.1:8001/v1", "enabled": True}},
        "overrides": {"openai/gpt-4o": {"cost": {"input_per_1m": 0.01}}},
        "extra": [{"id": "ollama/my:latest", "provider": "ollama", "name": "My",
                   "capabilities": {"tool_use": True, "vision": False, "reasoning": False}}],
    }
    (iso / "model_catalog.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    cfg = catalog_db.load_config()
    assert cfg["default_model"] == "ollama/my:latest"
    assert cfg["providers"]["vllm"]["api_base"].endswith("/v1")
    assert cfg["providers"]["vllm"]["enabled"] is True
    assert cfg["overrides"]["openai/gpt-4o"]["cost"]["input_per_1m"] == 0.01
    assert cfg["extra"][0]["id"] == "ollama/my:latest"
    # JSON 保留不动（仅备份，不再写入）
    assert (iso / "model_catalog.json").exists()
    # DB 首次导入后不再重复（幂等）
    assert catalog_db.load_config() == cfg


def test_save_roundtrip(iso):
    cfg = {
        "default_model": "deepseek/deepseek-v4-flash",
        "small_model": None,
        "voice_model_size": "0.6B",
        "providers": {"ollama": {"label": "Ollama", "api_base": "http://localhost:11434",
                                 "api_key": "", "enabled": False}},
        "overrides": {"openai/gpt-4o": {"name": "Overridden"}},
        "extra": [{"id": "x/y", "provider": "x", "name": "X"}],
    }
    catalog_db.save_config(cfg)
    out = catalog_db.load_config()
    assert out["default_model"] == cfg["default_model"]
    assert out["voice_model_size"] == "0.6B"
    # None 不落库（回退语义：未配置）
    assert "small_model" not in out
    assert out["providers"]["ollama"]["enabled"] is False
    assert out["providers"]["ollama"]["api_base"].startswith("http://localhost")
    assert out["overrides"]["openai/gpt-4o"]["name"] == "Overridden"
    assert out["extra"][0]["id"] == "x/y"


def test_import_export(iso):
    catalog_db.save_config({"default_model": "a/b"})
    exp = catalog_db.export_config()
    assert exp["default_model"] == "a/b"
    # 导入覆盖写库，未知键被丢弃
    catalog_db.import_config({"default_model": "c/d", "junk": 1,
                              "providers": {"p": {"label": "P", "api_base": "http://x/v1"}}})
    out = catalog_db.load_config()
    assert out["default_model"] == "c/d"
    assert "junk" not in out
    assert out["providers"]["p"]["label"] == "P"
    with pytest.raises(ValueError):
        catalog_db.import_config(["not", "a", "dict"])


def test_api_key_not_leaked_into_provider_extra(iso):
    catalog_db.save_config({"providers": {"deepseek": {"label": "D", "api_base": "https://api.deepseek.com",
                                                       "api_key": "sk-test", "enabled": True}}})
    cfg = catalog_db.load_config()
    assert cfg["providers"]["deepseek"]["api_key"] == "sk-test"
    assert set(cfg["providers"]["deepseek"].keys()) == {"label", "api_base", "api_key", "enabled"}


# ── 悬挂默认位自愈：删除模型/Provider 后 default/small/image 不再指向已不存在条目 ──

_FAKE_CATALOG = [{"id": "deepseek/deepseek-v4-flash", "provider": "deepseek",
                  "name": "F", "default": True}]


def _stub_catalog(monkeypatch):
    monkeypatch.setattr("app.models.catalog.get_catalog", lambda force=False: list(_FAKE_CATALOG))


def test_remove_custom_model_clears_dangling_defaults(iso, no_probes, monkeypatch):
    catalog.save_config({
        "default_model": "custom/probe",
        "small_model": "custom/probe",
        "image_caption_model": "custom/probe",
        "extra": [{"id": "custom/probe", "provider": "custom", "name": "C"}],
    })
    removed = catalog.remove_custom_model("custom/probe")
    assert removed is True
    cfg = catalog_db.load_config()
    assert not cfg.get("default_model")
    assert not cfg.get("small_model")
    assert not cfg.get("image_caption_model")
    assert catalog.lookup("custom/probe") is None
    # getter 回退到内置默认标记条目，不再返回悬挂 id
    _stub_catalog(monkeypatch)
    assert catalog.default_model() == "deepseek/deepseek-v4-flash"


def test_remove_provider_survives_when_declarative_model_kept(iso, no_probes, monkeypatch):
    """extra 条目（声明式、与 provider 注册表无关）保留时，指向它的默认位不清除。"""
    catalog.save_config({
        "default_model": "mypro/m1",
        "providers": {"mypro": {"label": "P", "api_base": "", "enabled": True}},
        "extra": [{"id": "mypro/m1", "provider": "mypro", "name": "M"}],
    })
    removed = catalog.remove_provider("mypro")
    assert removed is True
    cfg = catalog_db.load_config()
    assert cfg.get("default_model") == "mypro/m1"


def test_getters_ignore_dangling_default_db_value(iso, monkeypatch):
    """手工写库制造悬挂默认（绕过删除路径）：getter 运行时守卫降级，不报错。"""
    catalog_db.save_config({
        "default_model": "ghost/gone",
        "small_model": "ghost/gone",
        "image_caption_model": "ghost/gone",
    })
    _stub_catalog(monkeypatch)
    assert catalog.default_model() != "ghost/gone"
    assert catalog.default_model() == "deepseek/deepseek-v4-flash"
    assert catalog.small_model() == catalog.default_model()
    assert catalog.image_caption_model() is None


def _entries():
    conn = catalog_db._connect()
    try:
        return [tuple(r) for r in conn.execute("SELECT id, kind FROM catalog_entries").fetchall()]
    finally:
        conn.close()


def test_reedit_extra_model_promotes_to_override_no_duplicate(iso, no_probes):
    """编辑「额外(extra)模型」第二次：升级为 override 且不留 duplicate，不再 500。"""
    entry = {"id": "ollama/my-custom:latest", "provider": "ollama", "name": "My",
             "capabilities": {"tool_use": True, "vision": False, "reasoning": False}}
    first = catalog.upsert_custom_model(dict(entry))
    assert first["id"] == "ollama/my-custom:latest"
    cfg = catalog_db.load_config()
    assert [e["id"] for e in (cfg.get("extra") or [])] == ["ollama/my-custom:latest"]
    # 二次编辑（编辑表单回填完整条目）→ upgrade 到 overrides，extra 里移除，不炸
    entry["description"] = "edited"
    catalog.upsert_custom_model(dict(entry))
    cfg = catalog_db.load_config()
    assert "ollama/my-custom:latest" in (cfg.get("overrides") or {})
    assert "ollama/my-custom:latest" not in [e.get("id") for e in (cfg.get("extra") or [])]
    ids = _entries()
    assert ids.count(("ollama/my-custom:latest", "override")) == 1
    assert ids.count(("ollama/my-custom:latest", "extra")) == 0


def test_write_config_dedup_id_in_both_override_and_extra(iso):
    """写入端兜底：legacy/corrupt cfg 同一 id 同时出现在 overrides 与 extra 时去重不炸。"""
    catalog_db.save_config({
        "overrides": {"x/y": {"name": "O"}},
        "extra": [{"id": "x/y", "provider": "x", "name": "E"}],
    })
    cfg = catalog_db.load_config()
    assert cfg["overrides"]["x/y"]["name"] == "O"
    assert [e["id"] for e in (cfg.get("extra") or [])] == []  # extra 重复者被跳过