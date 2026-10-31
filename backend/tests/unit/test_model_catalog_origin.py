"""模型目录来源标注（origin）与 Provider 模型计数口径。

背景：前端「模型管理 → 模型列表」只展示手动添加（override/extra）与已配置
Provider 实际返回的模型；本机 Ollama 探测（local）与内置目录（builtin）不展示。
这些测试锁定后端 origin 判定与 Provider「模型数」的一致性。
"""

from __future__ import annotations

import json
import urllib.request


def test_annotate_origin_precedence():
    from models import catalog

    entries = [
        {"id": "a/manual-override", "origin_provider": "stale"},
        {"id": "b/manual-extra"},
        {"id": "c/local-probe"},
        {"id": "p/seen"},
        {"id": "builtin/untouched"},
    ]
    out = catalog.annotate_origin(
        entries,
        cfg={"overrides": {"a/manual-override": {"name": "x"}},
             "extra": [{"id": "b/manual-extra"}]},
        local_ids={"c/local-probe"},
        provider_seen={"p": {"p/seen", "a/manual-override"}},
    )
    origins = {e["id"]: e["origin"] for e in out}
    assert origins == {
        "a/manual-override": "override",
        "b/manual-extra": "extra",
        "c/local-probe": "local",
        "p/seen": "provider",
        "builtin/untouched": "builtin",
    }
    provider_entry = next(e for e in out if e["id"] == "p/seen")
    assert provider_entry["origin_provider"] == "p"
    # 用户 override 优先于 Provider seen
    assert "origin_provider" not in next(e for e in out if e["id"] == "a/manual-override")


def test_probe_provider_records_seen_including_known_ids(monkeypatch):
    from models import catalog

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"data": [{"id": "m1"}, {"id": "m2"}]}).encode("utf-8")

    monkeypatch.setattr(catalog, "local_endpoint_alive", lambda _base: True)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: _Resp())
    monkeypatch.setattr(catalog, "_probe_cache", {})
    monkeypatch.setattr(catalog, "_provider_seen", {})

    extras = catalog.probe_provider_models(
        "prov", "http://127.0.0.1:8001/v1", "", {"prov/m1"}, force=True
    )

    assert [e["id"] for e in extras] == ["prov/m2"]
    # 已收录（known）的 id 虽然不产生增量，但端点确实返回过，必须进入 seen。
    assert catalog._provider_seen["prov"] == {"prov/m1", "prov/m2"}


def test_probe_cache_hit_restores_seen_and_filters_known(monkeypatch):
    from models import catalog

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"data": [{"id": "m1"}, {"id": "m2"}]}).encode("utf-8")

    monkeypatch.setattr(catalog, "local_endpoint_alive", lambda _base: True)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: _Resp())
    monkeypatch.setattr(catalog, "_probe_cache", {})
    monkeypatch.setattr(catalog, "_provider_seen", {})

    assert len(catalog.probe_provider_models("prov", "http://127.0.0.1:8001", "", set())) == 2
    catalog._provider_seen.clear()

    extras = catalog.probe_provider_models("prov", "http://127.0.0.1:8001", "", {"prov/m1"})

    assert [e["id"] for e in extras] == ["prov/m2"]
    assert catalog._provider_seen["prov"] == {"prov/m1", "prov/m2"}


def test_get_catalog_annotates_builtin_local_and_provider(monkeypatch):
    from models import catalog

    monkeypatch.setattr(catalog, "_catalog_cache", {})
    monkeypatch.setattr(catalog, "_ollama_native", {})
    monkeypatch.setattr(catalog, "_provider_seen", {"p": {"p/seen", "p/new", "builtin/x"}})
    monkeypatch.setattr(catalog, "build_catalog", lambda: [
        {"id": "builtin/x"},
        {"id": "builtin/y"},
    ])
    monkeypatch.setattr(catalog, "probe_ollama_models", lambda known, force=False: [
        {"id": "ollama_chat/local"},
    ])
    monkeypatch.setattr(catalog, "probe_configured_providers", lambda known, force=False: [
        {"id": "p/new"},
    ])
    monkeypatch.setattr(catalog, "read_hidden", lambda: set())
    monkeypatch.setattr(catalog, "load_config", lambda: {
        "overrides": {},
        "extra": [{"id": "custom/manual"}],
    })

    origins = {e["id"]: e["origin"] for e in catalog.get_catalog(force_reload=True)}

    assert origins["builtin/x"] == "provider"  # 端点实际返回过内置 id
    assert origins["builtin/y"] == "builtin"
    assert origins["ollama_chat/local"] == "local"
    assert origins["p/new"] == "provider"
    # extra 里声明但 build_catalog 未产出的条目不会凭空出现，仅验证已有条目来源。
    assert "custom/manual" not in origins


def test_provider_models_source_counts_only_managed(monkeypatch):
    from models import catalog

    entries = [
        {"id": "p/seen-builtin", "origin": "provider", "origin_provider": "p"},
        {"id": "p/manual", "origin": "override"},
        {"id": "p/untouched-builtin", "origin": "builtin"},
        {"id": "q/other", "origin": "extra"},
    ]
    monkeypatch.setattr(catalog, "get_catalog", lambda force_reload=False: entries)
    monkeypatch.setattr(catalog, "read_providers", lambda: {
        "p": {"label": "P", "api_base": "http://x", "enabled": True, "type": "openai"},
    })

    rows = catalog.provider_models_source()

    assert [e["id"] for e in rows[0]["models"]] == ["p/seen-builtin", "p/manual"]
