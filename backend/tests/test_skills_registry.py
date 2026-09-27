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


# ── 内置技能播种（bundled）────────────────────────────────────────────────

@pytest.fixture
def fake_bundled(tmp_path, monkeypatch):
    """把内置技能目录指到 tmp：内容最小化，只测播种语义，不测随包内容。"""
    b = tmp_path / "bundled"
    for cat, names in (("engineering", ("alpha", "beta")), ("productivity", ("gamma",))):
        for n in names:
            d = b / cat / n
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(
                f"---\nname: {n}\ndescription: {n} desc\n---\n# {n}\n", encoding="utf-8")
    (b / "engineering" / "alpha" / "helper.md").write_text("配套文件", encoding="utf-8")
    (b / "engineering" / "README.md").write_text("# 分类说明，不是技能", encoding="utf-8")
    monkeypatch.setattr(registry, "bundled_skills_dir", lambda: b)
    return b


def test_bundled_dir_is_inside_package():
    """内置目录必须在 app/skills/bundled（包内随代码分发）——data/ 被 .gitignore 忽略。"""
    d = registry.bundled_skills_dir()
    assert d.name == "bundled"
    assert d.parent.name == "skills"
    assert d.parent.parent.name == "app"


def test_bundled_content_ships_with_package():
    """随包内置内容真的存在（Skills_Real_Engineers，28 个技能按分类嵌套）。"""
    d = registry.bundled_skills_dir()
    assert d.is_dir(), "内置技能目录缺失"
    names = registry._discover_bundled()
    assert len(names) >= 20, f"内置技能过少: {len(names)}"
    # 嵌套两层（<分类>/<名字>/SKILL.md）必须能被播种发现
    assert "tdd" in names and "triage" in names and "handoff" in names


def test_discover_bundled_finds_nested_and_skips_readme(fake_bundled):
    found = registry._discover_bundled()
    assert sorted(found) == ["alpha", "beta", "gamma"]
    assert "README" not in found


def test_seed_bundled_first_run_copies_tree(tmp_path, fake_bundled):
    managed = tmp_path / "data" / "skills"
    assert sorted(registry.seed_bundled(managed)) == ["alpha", "beta", "gamma"]
    assert (managed / "alpha" / "SKILL.md").is_file()
    # 配套文件（技能可能带 scripts/、references/）必须随目录一起搬运
    assert (managed / "alpha" / "helper.md").read_text("utf-8") == "配套文件"
    assert sorted(registry.bundled_names(managed)) == ["alpha", "beta", "gamma"]


def test_seed_bundled_is_idempotent(tmp_path, fake_bundled):
    """幂等：重复启动零拷贝。"""
    managed = tmp_path / "data" / "skills"
    registry.seed_bundled(managed)
    assert registry.seed_bundled(managed) == []
    assert len([p for p in managed.iterdir() if p.is_dir()]) == 3


def test_seed_bundled_does_not_overwrite_user_edits(tmp_path, fake_bundled):
    """非破坏：用户改过的内置技能，升级/重启都不能被冲掉。"""
    managed = tmp_path / "data" / "skills"
    registry.seed_bundled(managed)
    f = managed / "alpha" / "SKILL.md"
    f.write_text("---\nname: alpha\ndescription: 我改过的\n---\n我的内容", encoding="utf-8")
    registry.seed_bundled(managed)
    after = f.read_text("utf-8")
    assert "我改过的" in after and "我的内容" in after


def test_seed_bundled_respects_deletion(tmp_path, fake_bundled):
    """尊重删除：用户删掉的内置技能不会在下次启动复活。"""
    import shutil
    managed = tmp_path / "data" / "skills"
    registry.seed_bundled(managed)
    shutil.rmtree(managed / "beta")
    registry.seed_bundled(managed)
    assert not (managed / "beta").exists()


def test_seed_bundled_preserves_preexisting_same_name(tmp_path, fake_bundled):
    """受管库里已存在的同名内容（自建技能）不覆盖，但仍标记为已处理以免每次重试。"""
    managed = tmp_path / "data" / "skills"
    (managed / "alpha").mkdir(parents=True)
    (managed / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: 我自己写的\n---\nx", encoding="utf-8")
    assert registry.seed_bundled(managed) == ["beta", "gamma"]
    assert "我自己写的" in (managed / "alpha" / "SKILL.md").read_text("utf-8")
    assert "alpha" in registry.bundled_names(managed)


def test_seed_bundled_version_bump_adds_new_only(tmp_path, fake_bundled, monkeypatch):
    """BUNDLED_VERSION 递增可补种新增技能，已存在的一律不动。"""
    managed = tmp_path / "data" / "skills"
    registry.seed_bundled(managed)
    d = fake_bundled / "engineering" / "delta"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: delta\ndescription: 新增\n---\n# d", encoding="utf-8")
    monkeypatch.setattr(registry, "BUNDLED_VERSION", registry.BUNDLED_VERSION + 1)
    assert registry.seed_bundled(managed) == ["delta"]
    assert registry.seed_bundled(managed) == []


def test_seed_bundled_without_bundled_dir_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "bundled_skills_dir", lambda: tmp_path / "nope")
    assert registry.seed_bundled(tmp_path / "data" / "skills") == []
