"""统一数据根（storage/paths.py）的解析与旧数据迁移测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from config import settings
from storage import paths


def _fake_backend(tmp_path: Path) -> Path:
    backend = tmp_path / "backend"
    (backend / "data").mkdir(parents=True)
    (backend / "data" / "session.db").write_text("legacy", encoding="utf-8")
    return backend


def test_data_root_defaults_to_platform_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "agentsuper_data", "")
    if sys.platform.startswith("win"):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        assert paths.data_root() == tmp_path / "local" / "agentsuper"
    elif sys.platform == "darwin":
        assert paths.data_root() == Path.home() / "Library" / "Application Support" / "agentsuper"
    else:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        assert paths.data_root() == tmp_path / "xdg" / "agentsuper"


def test_explicit_root_and_subdirs_follow(monkeypatch, tmp_path):
    root = tmp_path / "custom-root"
    monkeypatch.setattr(settings, "agentsuper_data", str(root))
    monkeypatch.setattr(settings, "agentsuper_cache", "")
    monkeypatch.setattr(settings, "agentsuper_config", "")
    monkeypatch.setattr(settings, "agentsuper_state", "")
    monkeypatch.setattr(settings, "agentsuper_log", "")
    monkeypatch.setattr(settings, "agentsuper_bin", "")
    monkeypatch.setattr(settings, "agentsuper_migrate_legacy", False)

    got = paths.global_paths()
    assert got["data"] == root
    assert got["cache"] == root / "cache"
    assert got["config"] == root / "config"
    assert got["state"] == root / "state"
    assert got["log"] == root / "logs"
    assert got["bin"] == root / "bin"
    for p in got.values():
        assert p.is_dir()


def test_subdir_override_and_relative_resolution(monkeypatch, tmp_path):
    root = tmp_path / "root"
    monkeypatch.setattr(settings, "agentsuper_data", str(root))
    monkeypatch.setattr(settings, "agentsuper_log", "my-logs")
    monkeypatch.setattr(settings, "agentsuper_bin", str(tmp_path / "absolute-bin"))
    monkeypatch.setattr(settings, "agentsuper_migrate_legacy", False)

    got = paths.global_paths()
    assert got["log"] == root / "my-logs"
    assert got["bin"] == tmp_path / "absolute-bin"


def test_data_path_strips_legacy_data_prefix(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "agentsuper_data", str(tmp_path / "root"))
    assert paths.data_path("data/session.db", "session.db") == tmp_path / "root" / "session.db"
    assert paths.data_path("session.db", "x") == tmp_path / "root" / "session.db"
    absolute = tmp_path / "elsewhere" / "db.sqlite"
    assert paths.data_path(str(absolute), "x") == absolute
    assert paths.data_path("", "metrics.jsonl") == tmp_path / "root" / "metrics.jsonl"


def test_global_paths_with_explicit_base_skips_migration(monkeypatch, tmp_path):
    backend = _fake_backend(tmp_path)
    monkeypatch.setattr(paths, "backend_dir", lambda: backend)
    monkeypatch.setattr(settings, "agentsuper_migrate_legacy", True)

    root = tmp_path / "explicit"
    got = paths.global_paths(root)
    assert got["data"] == root
    assert not (root / "session.db").exists()


def test_first_start_copies_legacy_data(monkeypatch, tmp_path):
    backend = _fake_backend(tmp_path)
    monkeypatch.setattr(paths, "backend_dir", lambda: backend)
    root = tmp_path / "os-root"
    monkeypatch.setattr(settings, "agentsuper_data", str(root))
    monkeypatch.setattr(settings, "agentsuper_migrate_legacy", True)

    paths.global_paths()
    assert (root / "session.db").read_text(encoding="utf-8") == "legacy"
    assert (root / paths._MIGRATE_MARKER).exists()
    assert (backend / "data" / "session.db").exists()

    # 幂等：删除新根文件后再次启动不应重拷（标记已存在）。
    (root / "session.db").unlink()
    paths.global_paths()
    assert not (root / "session.db").exists()


def test_migration_skips_non_empty_root(monkeypatch, tmp_path):
    backend = _fake_backend(tmp_path)
    monkeypatch.setattr(paths, "backend_dir", lambda: backend)
    root = tmp_path / "os-root"
    root.mkdir()
    (root / "keep.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(settings, "agentsuper_data", str(root))
    monkeypatch.setattr(settings, "agentsuper_migrate_legacy", True)

    paths.global_paths()
    assert not (root / "session.db").exists()
    assert not (root / paths._MIGRATE_MARKER).exists()


def test_migration_treats_empty_subdirs_as_empty(monkeypatch, tmp_path):
    backend = _fake_backend(tmp_path)
    monkeypatch.setattr(paths, "backend_dir", lambda: backend)
    root = tmp_path / "os-root"
    (root / "cache").mkdir(parents=True)
    (root / "state").mkdir(parents=True)
    monkeypatch.setattr(settings, "agentsuper_data", str(root))
    monkeypatch.setattr(settings, "agentsuper_migrate_legacy", True)

    paths.global_paths()
    assert (root / "session.db").read_text(encoding="utf-8") == "legacy"


def test_migration_disabled(monkeypatch, tmp_path):
    backend = _fake_backend(tmp_path)
    monkeypatch.setattr(paths, "backend_dir", lambda: backend)
    root = tmp_path / "os-root"
    monkeypatch.setattr(settings, "agentsuper_data", str(root))
    monkeypatch.setattr(settings, "agentsuper_migrate_legacy", False)

    paths.global_paths()
    assert not (root / "session.db").exists()
    assert not (root / paths._MIGRATE_MARKER).exists()


def test_migration_skips_when_lock_held(monkeypatch, tmp_path):
    backend = _fake_backend(tmp_path)
    monkeypatch.setattr(paths, "backend_dir", lambda: backend)
    root = tmp_path / "os-root"
    root.mkdir()
    (root / paths._MIGRATE_LOCK).write_text("locked", encoding="utf-8")
    monkeypatch.setattr(settings, "agentsuper_data", str(root))
    monkeypatch.setattr(settings, "agentsuper_migrate_legacy", True)

    paths.global_paths()
    assert not (root / "session.db").exists()
