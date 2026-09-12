"""模型目录数据库存储（SQLite）：替代 data/model_catalog.json 的单一事实来源。

设计：
- 专用库 `data/model_catalog.db`（与 session.db 解耦，便于单独备份/迁移）。
- 表结构：
    catalog_settings(key, value)            —— default_model / small_model / image_caption_model / voice_model_size
    providers(name, label, api_base, api_key, enabled, data) —— 前端配置的 Provider 注册表
    catalog_entries(id, kind, data)         —— kind=override（深合并内置）/ extra（追加自定义）
- 兼容层：`load_config()` / `save_config()` 仍返回 JSON 形态 dict，供 catalog.py 既有逻辑复用。
- 迁移：首次连接某库时，若三表皆空且存在旧 `model_catalog.json`，自动导入（JSON 保留不动作备份）。
- 导出/导入：`export_config()` / `import_config()` 供迁移到别的机器。

并发：每次操作新开连接（低频配置写，SQLite 本地开销可忽略），WAL + busy_timeout；
初始化按「库路径」记录，环境变量切换 data 目录（测试/多租户）时各自独立导入。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS providers (
  name     TEXT PRIMARY KEY,
  label    TEXT NOT NULL DEFAULT '',
  api_base TEXT NOT NULL DEFAULT '',
  api_key  TEXT NOT NULL DEFAULT '',
  enabled  INTEGER NOT NULL DEFAULT 1,
  data     TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS catalog_entries (
  id   TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  data TEXT NOT NULL
);
"""

_SETTING_KEYS = ("default_model", "small_model", "image_caption_model", "voice_model_size")

_lock = threading.RLock()
_initialized_paths: set[str] = set()


def _data_dir() -> Path:
    from app.storage.paths import global_paths
    return global_paths()["data"]


def catalog_json_path() -> Path:
    """旧 JSON 覆盖文件（迁移来源 / 备份，DB 生效后不再写入）。"""
    return _data_dir() / "model_catalog.json"


def catalog_db_path() -> Path:
    """模型目录数据库路径。"""
    return _data_dir() / "model_catalog.db"


def _connect() -> sqlite3.Connection:
    p = catalog_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.executescript(_SCHEMA)
    key = str(p)
    if key not in _initialized_paths:
        _initialized_paths.add(key)
        _import_json_if_empty(conn)
    return conn


def init() -> None:
    """幂等初始化（建表 + 首次从 JSON 导入），供应用启动时调用。"""
    with _lock:
        conn = _connect()
        try:
            conn.commit()
        finally:
            conn.close()


def _import_json_if_empty(conn: sqlite3.Connection) -> None:
    """首次连接：三表皆空且存在旧 JSON 时导入（JSON 保留不动）。"""
    n = conn.execute(
        "SELECT (SELECT COUNT(*) FROM catalog_settings)"
        " + (SELECT COUNT(*) FROM providers)"
        " + (SELECT COUNT(*) FROM catalog_entries)"
    ).fetchone()[0]
    if n:
        return
    p = catalog_json_path()
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if isinstance(data, dict):
        _write_config(conn, data)
        conn.commit()


def _read_config(conn: sqlite3.Connection) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    for row in conn.execute("SELECT key, value FROM catalog_settings"):
        try:
            v = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            v = row["value"]
        if v is not None:
            cfg[row["key"]] = v

    providers: dict[str, Any] = {}
    for row in conn.execute("SELECT * FROM providers"):
        entry: dict[str, Any] = {
            "label": row["label"],
            "api_base": row["api_base"],
            "api_key": row["api_key"],
            "enabled": bool(row["enabled"]),
        }
        try:
            extra = json.loads(row["data"] or "{}")
        except (json.JSONDecodeError, TypeError):
            extra = {}
        if isinstance(extra, dict):
            for k, v in extra.items():
                if k not in entry:
                    entry[k] = v
        providers[row["name"]] = entry
    if providers:
        cfg["providers"] = providers

    overrides: dict[str, Any] = {}
    extras: list[dict[str, Any]] = []
    for row in conn.execute("SELECT id, kind, data FROM catalog_entries"):
        try:
            d = json.loads(row["data"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(d, dict):
            continue
        if row["kind"] == "override":
            overrides[row["id"]] = d
        elif row["kind"] == "extra":
            extras.append(d)
    if overrides:
        cfg["overrides"] = overrides
    if extras:
        cfg["extra"] = extras
    return cfg


def _write_config(conn: sqlite3.Connection, cfg: dict[str, Any]) -> None:
    with conn:
        conn.execute("DELETE FROM catalog_settings")
        for key in _SETTING_KEYS:
            if key in cfg:
                conn.execute(
                    "INSERT INTO catalog_settings(key, value) VALUES (?, ?)",
                    (key, json.dumps(cfg.get(key), ensure_ascii=False)),
                )

        conn.execute("DELETE FROM providers")
        for name, p in (cfg.get("providers") or {}).items():
            p = dict(p or {})
            known = ("label", "api_base", "api_key", "enabled")
            extra = {k: v for k, v in p.items() if k not in known}
            conn.execute(
                "INSERT INTO providers(name, label, api_base, api_key, enabled, data)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (name, str(p.get("label") or ""), str(p.get("api_base") or ""),
                 str(p.get("api_key") or ""), 1 if p.get("enabled", True) else 0,
                 json.dumps(extra, ensure_ascii=False)),
            )

        conn.execute("DELETE FROM catalog_entries")
        seen_ids: set[str] = set()
        for mid, patch in (cfg.get("overrides") or {}).items():
            if mid in seen_ids:
                continue
            conn.execute(
                "INSERT INTO catalog_entries(id, kind, data) VALUES (?, 'override', ?)",
                (mid, json.dumps(patch, ensure_ascii=False)),
            )
            seen_ids.add(mid)
        for e in (cfg.get("extra") or []):
            mid = e.get("id") if isinstance(e, dict) else None
            if not mid or mid in seen_ids:
                continue
            conn.execute(
                "INSERT INTO catalog_entries(id, kind, data) VALUES (?, 'extra', ?)",
                (mid, json.dumps(e, ensure_ascii=False)),
            )
            seen_ids.add(mid)


def load_config() -> dict[str, Any]:
    """读取模型配置（DB 形态的 JSON dict；空库返回 {}）。"""
    with _lock:
        conn = _connect()
        try:
            return _read_config(conn)
        finally:
            conn.close()


def save_config(cfg: dict[str, Any]) -> None:
    """整体写回模型配置（保留未涉及字段由调用方保证：cfg 来自 load_config）。"""
    with _lock:
        conn = _connect()
        try:
            _write_config(conn, cfg)
        finally:
            conn.close()


def export_config() -> dict[str, Any]:
    """导出当前配置（迁移载体，与旧 model_catalog.json 同构）。"""
    return load_config()


def import_config(data: dict[str, Any]) -> dict[str, Any]:
    """导入配置（覆盖写库）。非法输入抛 ValueError。"""
    if not isinstance(data, dict):
        raise ValueError("config must be a JSON object")
    allowed = {"default_model", "small_model", "image_caption_model",
               "voice_model_size", "providers", "overrides", "extra"}
    clean = {k: v for k, v in data.items() if k in allowed}
    save_config(clean)
    return clean
