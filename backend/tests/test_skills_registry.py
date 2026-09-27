# -*- coding: utf-8 -*-
"""技能源注册表（受管库 + 外部源 + 旧格式迁移）测试。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.skills import registry  # noqa: E402


@pytest.fixture
def fake_backend(tmp_path, monkeypatch):
    """把 registry 的 backend 根指到 tmp，隔离真实 backend/data。"""
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(registry, "backend_dir", lambda: tmp_path)
    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(registry, "managed_skills_dir", lambda: tmp_path / "data" / "skills")
    monkeypatch.setattr(registry, "_sources_path", lambda: tmp_path / "data" / "skills_sources.json")
    monkeypatch.setattr(registry, "LEGACY_SOURCES_FILE", "data/runtime_skills_dir.json")
    return tmp_path


def test_default_managed_dir_needs_no_config(fake_backend):
    """开箱即用：没有任何配置文件时回退到 data/skills（由调用方创建）。"""
    managed, extras = registry.load_sources()
    assert managed == str(fake_backend / "data" / "skills")
    assert extras == []


def test_managed_dir_is_auto_created(fake_backend):
    """[回归] 受管库启动时自动创建 → 不再出现「技能目录不存在，跳过加载」WARNING。"""
    from app.skills.loader import SkillLoader
    managed, extras = registry.load_sources()
    loader = SkillLoader(managed, create=True, extra_dirs=extras)
    assert Path(managed).is_dir()
    assert loader.load_all() == []


def test_save_and_reload_roundtrip(fake_backend):
    managed = str(fake_backend / "data" / "skills")
    ext = str(fake_backend / "my-skills")
    registry.save_sources(managed, [ext, ext, managed])  # 去重 + 排除自身
    m2, e2 = registry.load_sources()
    assert m2 == managed
    assert e2 == [ext], e2  # 重复项与受管库本身被去掉


def test_legacy_file_migrates_to_external_source(fake_backend):
    """旧版 runtime_skills_dir.json 的 directory 迁移为**外部源**（不丢用户已选目录）。"""
    legacy = fake_backend / "data" / "runtime_skills_dir.json"
    legacy.write_text(json.dumps({"directory": r"D:\old-skills"}), encoding="utf-8")
    managed, extras = registry.load_sources()
    assert managed == str(fake_backend / "data" / "skills")
    assert r"D:\old-skills" in extras


def test_legacy_backend_skills_dir_becomes_external(fake_backend):
    """早期默认目录 backend/skills 若存在 → 纳入外部源（兼容手工放置的技能）。"""
    (fake_backend / "skills").mkdir()
    (fake_backend / "skills" / "hand.md").write_text("---\nname: hand\n---\nx\n", encoding="utf-8")
    managed, extras = registry.load_sources()
    assert extras == [str(fake_backend / "skills")]


def test_save_removes_legacy_file(fake_backend):
    """迁移完成后删除旧文件，避免每次启动重复迁移。"""
    legacy = fake_backend / "data" / "runtime_skills_dir.json"
    legacy.write_text(json.dumps({"directory": r"D:\old-skills"}), encoding="utf-8")
    registry.save_sources(str(fake_backend / "data" / "skills"), [r"D:\old-skills"])
    assert not legacy.exists()
    _, extras = registry.load_sources()
    assert extras == [r"D:\old-skills"]


def test_corrupt_file_falls_back(fake_backend):
    (fake_backend / "data" / "skills_sources.json").write_text("{ not json", encoding="utf-8")
    managed, extras = registry.load_sources()
    assert managed == str(fake_backend / "data" / "skills")
    assert extras == []
