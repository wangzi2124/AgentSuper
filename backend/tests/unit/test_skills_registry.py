"""技能源注册表：默认只加载受管库；旧 legacy 外部源只清理一次。"""

import json
from pathlib import Path

from app.skills import registry


def _isolate(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    backend = tmp_path / "backend"
    (backend / "skills").mkdir(parents=True)
    monkeypatch.setattr(registry, "data_dir", lambda: data)
    monkeypatch.setattr(registry, "backend_dir", lambda: backend)
    return backend


def test_backend_skills_is_not_auto_added(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    managed, extras = registry.load_sources()
    assert extras == []
    assert managed == str(tmp_path / "data" / "skills")


def test_legacy_sources_file_cleans_backend_skills_once(tmp_path, monkeypatch):
    backend = _isolate(tmp_path, monkeypatch)
    other = tmp_path / "other"
    sources = tmp_path / "data" / "skills_sources.json"
    sources.write_text(
        json.dumps({
            "managed": str(tmp_path / "data" / "skills"),
            "extra_dirs": [str(backend / "skills"), str(other)],
        }),
        encoding="utf-8",
    )

    _, extras = registry.load_sources()
    assert [str(Path(d).resolve()) for d in extras] == [str(other.resolve())]
    saved = json.loads(sources.read_text(encoding="utf-8"))
    assert saved["version"] == registry.SOURCES_VERSION
    assert saved["extra_dirs"] == [str(other)]


def test_manual_readd_after_migration_is_kept(tmp_path, monkeypatch):
    backend = _isolate(tmp_path, monkeypatch)
    managed, _ = registry.load_sources()
    registry.save_sources(managed, [str(backend / "skills")])

    _, extras = registry.load_sources()
    assert [str(Path(d).resolve()) for d in extras] == [
        str((backend / "skills").resolve())
    ]


def test_bundled_includes_merged_library_skills():
    names = registry._discover_bundled()
    assert {"docx", "claude-api", "tdd"} <= set(names)
