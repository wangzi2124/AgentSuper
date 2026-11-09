"""Provider 写操作的定向刷新：只探测目标 Provider，删除不触发探测。

这些用例防止回退成「每次 Provider 增删改都强制重探全部 Provider」——本地 Ollama
探测一次约 4s，两个 Provider 串行可达 8s+，会拖慢接口并阻塞事件循环。
"""

from __future__ import annotations

from models import catalog


def _setup(monkeypatch, cfg, snapshot):
    monkeypatch.setattr(catalog, "load_config", lambda: cfg)
    monkeypatch.setattr(catalog, "save_config", lambda c: cfg.update(c))
    monkeypatch.setattr(catalog, "_cache_key", lambda: "test-key")
    monkeypatch.setattr(catalog, "_catalog_cache", {"test-key": list(snapshot)})
    monkeypatch.setattr(catalog, "_probe_cache", {})
    monkeypatch.setattr(catalog, "_provider_seen", {})


def _fake_probe(probed, provider_name, api_base, api_key, known_ids, force=False):
    probed.append(provider_name)
    model_id = f"{provider_name}/m1"
    catalog._set_provider_seen(provider_name, {model_id})
    return [{
        "id": model_id,
        "provider": provider_name,
        "family": "m1",
        "name": model_id,
        "description": "",
        "capabilities": {},
        "context_length": 4096,
        "limits": {"max_output_tokens": 1024},
        "cost": {},
    }]


def test_upsert_provider_probes_only_target_provider(monkeypatch):
    cfg = {"providers": {}, "overrides": {}, "extra": [], "hidden": []}
    snapshot = [
        {"id": "deepseek/deepseek-v4-flash", "origin": "provider", "origin_provider": "deepseek"},
        {"id": "builtin/keep", "origin": "builtin"},
    ]
    _setup(monkeypatch, cfg, snapshot)

    probed: list[str] = []
    monkeypatch.setattr(catalog, "probe_provider_models", lambda *a, **k: _fake_probe(probed, *a, **k))
    monkeypatch.setattr(
        catalog, "probe_configured_providers",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应重探全部 Provider")),
    )
    monkeypatch.setattr(
        catalog, "reload_catalog",
        lambda: (_ for _ in ()).throw(AssertionError("不应全量重建目录")),
    )

    entry = catalog.upsert_provider(
        "newp", {"label": "New", "api_base": "http://127.0.0.1:9000/v1", "type": "openai"},
    )

    assert entry["label"] == "New"
    assert probed == ["newp"]
    by_id = {e["id"]: e for e in catalog._catalog_cache["test-key"]}
    assert "deepseek/deepseek-v4-flash" in by_id
    assert by_id["newp/m1"]["origin"] == "provider"
    assert by_id["newp/m1"]["origin_provider"] == "newp"


def test_upsert_provider_label_only_change_skips_probe(monkeypatch):
    cfg = {
        "providers": {"p": {"label": "Old", "api_base": "http://127.0.0.1:9000/v1",
                            "api_key": "", "enabled": True, "type": "openai"}},
        "overrides": {},
        "extra": [],
        "hidden": [],
    }
    snapshot = [
        {"id": "p/m1", "origin": "provider", "origin_provider": "p"},
        {"id": "builtin/keep", "origin": "builtin"},
    ]
    _setup(monkeypatch, cfg, snapshot)

    monkeypatch.setattr(
        catalog, "probe_provider_models",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("仅改标签不应探测")),
    )
    monkeypatch.setattr(
        catalog, "reload_catalog",
        lambda: (_ for _ in ()).throw(AssertionError("仅改标签不应全量重建")),
    )

    catalog.upsert_provider("p", {"label": "New"})

    assert cfg["providers"]["p"]["label"] == "New"
    assert {e["id"] for e in catalog._catalog_cache["test-key"]} == {"p/m1", "builtin/keep"}


def test_remove_provider_prunes_snapshot_without_probe(monkeypatch):
    cfg = {
        "providers": {
            "p": {"label": "P", "api_base": "http://127.0.0.1:9000/v1",
                  "api_key": "", "enabled": True, "type": "openai"},
            "q": {"label": "Q", "api_base": "http://127.0.0.1:9001/v1",
                  "api_key": "", "enabled": True, "type": "openai"},
        },
        "overrides": {},
        "extra": [],
        "hidden": [],
    }
    snapshot = [
        {"id": "p/m1", "origin": "provider", "origin_provider": "p"},
        {"id": "builtin/x", "origin": "provider", "origin_provider": "p"},
        {"id": "q/m1", "origin": "provider", "origin_provider": "q"},
    ]
    _setup(monkeypatch, cfg, snapshot)
    monkeypatch.setattr(catalog, "build_catalog", lambda cfg=None: [{"id": "builtin/x"}])

    monkeypatch.setattr(
        catalog, "probe_provider_models",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("删除不应探测")),
    )
    monkeypatch.setattr(
        catalog, "probe_ollama_provider_models",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("删除不应探测")),
    )
    monkeypatch.setattr(
        catalog, "reload_catalog",
        lambda: (_ for _ in ()).throw(AssertionError("删除不应全量重建")),
    )

    assert catalog.remove_provider("p") is True

    by_id = {e["id"]: e for e in catalog._catalog_cache["test-key"]}
    assert "p/m1" not in by_id
    assert by_id["q/m1"]["origin_provider"] == "q"
    # p 端点返回过的内置 id 随 provider 消失，回到 builtin（不再出现在管理列表）。
    assert by_id["builtin/x"]["origin"] == "builtin"
