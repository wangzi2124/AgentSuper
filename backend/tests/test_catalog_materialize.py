"""内建模型目录物化到 DB（catalog_db.seed_builtin / load_builtin_entries）单测。

用 AGENTSUPER_DATA 隔离到 tmp 目录；monkeypatch settings.db_type='sqlite'
确保不触碰真实 MySQL；对每个测试重置模块级 once-gate，保证幂等语义准确。
"""

from __future__ import annotations

import pytest

from app.models import catalog, catalog_db
from app.models.builtin_catalog import BUILTIN_CATALOG


_IDS = {e["id"] for e in BUILTIN_CATALOG}


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    """强制 sqlite + 隔离 data 目录，重置 once-gate。"""
    monkeypatch.setenv("AGENTSUPER_DATA", str(tmp_path))
    monkeypatch.setattr("app.models.catalog_db.backends.is_sqlite", lambda: True)
    # 重置 once-gate，确保每次测试走完整初始化
    catalog_db._initialized_paths.clear()
    catalog_db._json_imported_non_sqlite = False
    return tmp_path


@pytest.fixture(autouse=True)
def no_probes(monkeypatch):
    """stub 探测函数，避免网络请求干扰目录列表。"""
    monkeypatch.setattr("app.models.catalog.probe_ollama_models", lambda known, force=False: [])
    monkeypatch.setattr("app.models.catalog.probe_configured_providers", lambda known, force=False: [])


def _entries_by_id():
    conn = catalog_db._connect()
    try:
        return {
            r[0]: r[1]
            for r in conn.execute("SELECT id, kind FROM catalog_entries").fetchall()
        }
    finally:
        conn.close()


# ── seed 基础 ──────────────────────────────────────────────────────────────

def test_seed_builtin_populates_12_rows(iso):
    entries = catalog_db.load_builtin_entries()
    assert len(entries) == 12
    assert {e["id"] for e in entries} == _IDS


def test_seed_builtin_kind_is_builtin(iso):
    by_id = _entries_by_id()
    for mid in _IDS:
        assert by_id[mid] == "builtin"


def test_seed_builtin_idempotent_no_duplicate(iso):
    catalog_db._connect().close()
    catalog_db._connect().close()
    by_id = _entries_by_id()
    assert len(by_id) == 12


def test_deepseek_flash_has_default_flag_in_db(iso):
    entries = {e["id"]: e for e in catalog_db.load_builtin_entries()}
    assert entries["deepseek/deepseek-v4-flash"]["default"] is True


# ── DB 回退语义 ─────────────────────────────────────────────────────────────

def test_build_catalog_uses_db_builtin(iso, monkeypatch):
    """DB 有 builtin 行 → build_catalog 基于 DB（可检测 DB 修改）。"""
    # 手工改 DB 里 deepseek 名称，build_catalog 应反映 DB 内容
    conn = catalog_db._connect()
    try:
        import json as _json
        entry = dict(BUILTIN_CATALOG[0])
        entry["name"] = "DB-Flash"
        conn.execute(
            "UPDATE catalog_entries SET data = ? WHERE id = 'deepseek/deepseek-v4-flash'",
            (_json.dumps(entry, ensure_ascii=False),),
        )
        conn.commit()
    finally:
        conn.close()
    cat = {e["id"]: e for e in catalog.build_catalog()}
    assert cat["deepseek/deepseek-v4-flash"]["name"] == "DB-Flash"


def test_build_catalog_fallback_when_no_builtin(iso, monkeypatch):
    """DB 为空 → build_catalog 回退 BUILTIN_CATALOG 代码常量。"""
    conn = catalog_db._connect()
    try:
        conn.execute("DELETE FROM catalog_entries WHERE kind = 'builtin'")
        conn.commit()
    finally:
        conn.close()
    # 重置 build_catalog 的 lazy-import 缓存（_db_builtin_entries 无缓存，每次查库）
    cat = {e["id"]: e for e in catalog.build_catalog()}
    assert cat == {e["id"]: e for e in BUILTIN_CATALOG}


# ── seed 幂等 / 与已有行共存 ──────────────────────────────────────────────

def test_seed_skips_existing_id(iso):
    """迁移场景：某 id 已存在（extra 行）→ seed 跳过，不覆盖、不重复。"""
    import json as _json
    conn = catalog_db._connect()
    try:
        conn.execute("DELETE FROM catalog_entries WHERE id = ?", ("ollama/olmo-3:7b",))
        conn.execute(
            "INSERT INTO catalog_entries(id, kind, data) VALUES (?, 'extra', ?)",
            ("ollama/olmo-3:7b",
             _json.dumps({"id": "ollama/olmo-3:7b", "provider": "ollama",
                          "name": "User Olmo"}, ensure_ascii=False)),
        )
        conn.commit()
        catalog_db._seed_builtin(conn)
        conn.commit()
        by_id = _entries_by_id()
        assert by_id["ollama/olmo-3:7b"] == "extra"
    finally:
        conn.close()


def test_user_override_of_builtin_promotes_to_override_kind(iso):
    """用户编辑内置模型 → builtin 行升级为 override；base 回落代码常量补齐规格。"""
    import json as _json
    conn = catalog_db._connect()
    try:
        conn.execute("DELETE FROM catalog_entries WHERE id = ?", ("deepseek/deepseek-v4-flash",))
        conn.execute(
            "INSERT INTO catalog_entries(id, kind, data) VALUES (?, 'override', ?)",
            ("deepseek/deepseek-v4-flash",
             _json.dumps({"name": "Overridden Flash"}, ensure_ascii=False)),
        )
        conn.commit()
        catalog_db._seed_builtin(conn)
        conn.commit()
    finally:
        conn.close()
    cat = {e["id"]: e for e in catalog.build_catalog()}
    assert cat["deepseek/deepseek-v4-flash"]["name"] == "Overridden Flash"
    # 规格字段由代码常量 base 补齐（DB 无该内置行）
    assert cat["deepseek/deepseek-v4-flash"]["default"] is True
    by_id = _entries_by_id()
    assert by_id["deepseek/deepseek-v4-flash"] == "override"


# ── export_full ──────────────────────────────────────────────────────────────

def test_export_full_has_correct_shape(iso):
    catalog_db.save_config({
        "default_model": "deepseek/deepseek-v4-flash",
        "providers": {"deepseek": {"label": "D", "api_base": "https://x", "api_key": "", "enabled": True}},
    })
    full = catalog_db.export_full()
    assert "settings" in full and "providers" in full and "catalog_entries" in full
    assert full["settings"]["default_model"] == "deepseek/deepseek-v4-flash"
    assert full["providers"]["deepseek"]["api_base"] == "https://x"
    kinds = {e["kind"] for e in full["catalog_entries"]}
    assert "builtin" in kinds


def test_export_full_entries_count(iso):
    full = catalog_db.export_full()
    assert len(full["catalog_entries"]) == 12
