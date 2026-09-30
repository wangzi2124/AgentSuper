"""技能源注册表（受管库 + 外部源）——runtime 与 API 共用同一份解析逻辑。

设计（对齐 opencode Skill 语义 + 修掉「开箱 0 技能」）：
- **受管库**（managed）= `<backend>/data/skills`，启动时自动创建。用户在「技能」页
  新建的技能落在这里。它始终存在 → 不再出现「技能目录不存在，跳过加载」的启动 WARNING。
- **外部源**（extras）= 用户通过「选择技能文件夹」追加的目录（如 clone 的技能仓库），
  可有多个、可移除，**追加而非替换**，因此自建技能不会被切目录时弄丢。

状态持久化在 `<backend>/data/skills_sources.json`：
```json
{"managed": "<backend>/data/skills", "extra_dirs": ["D:/my-skills", ...]}
```
旧版状态文件 `data/runtime_skills_dir.json`（只有单个 `directory`）读取时自动迁移：
把那个目录当作**外部源**追加（受管库已覆盖「默认目录」语义），随后按新格式落盘。

**内置技能（bundled）**：`app/skills/bundled/` 是随包发布的开箱技能（Skills_Real_Engineers，
MIT）。启动时由 `seed_bundled()` 播种进受管库 —— 用户无需 clone，且作为受管技能可编辑/删除。
播种清单记在受管库的 `.bundled.json`（`{version, seeded: {身份名: {dir, digest}}}`），
因此：幂等（重复启动不重复拷贝）、非破坏（已存在的同名技能绝不覆盖）、尊重删除（用户
删掉的技能不会在下次启动时复活）。清单只记**实际复制过**的技能，用户自建的同名目录不会被
谎称成「内置」；`BUNDLED_VERSION` 递增时会刷新**用户未改动**的已播种技能内容。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

#: 受管库目录名（相对 backend/data/）
MANAGED_DIRNAME = "skills"
#: 新版状态文件（相对 backend/）
SOURCES_FILE = "data/skills_sources.json"
#: 旧版状态文件（相对 backend/），仅用于一次性迁移
LEGACY_SOURCES_FILE = "data/runtime_skills_dir.json"
#: 早期默认目录（相对 backend/）：用户可能手工放过技能，存在则作为外部源纳入
LEGACY_FALLBACK_DIRNAME = "skills"
#: 内置技能目录（相对 app/skills/）：随包发布、开箱可用
BUNDLED_DIRNAME = "bundled"
#: 内置播种清单文件名（位于受管库内）
BUNDLED_MANIFEST = ".bundled.json"
#: 内置技能集版本。**改动内置技能内容时递增**：启动时补种新增技能，并刷新**用户未改动**
#: 的已播种技能（摘要比对，见 `seed_bundled`）。v2 = 清单改存 `{name: {dir, digest}}`。
BUNDLED_VERSION = 2


def backend_dir() -> Path:
    """backend/ 根目录（app/skills/registry.py → parents[2]）。"""
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return backend_dir() / "data"


def managed_skills_dir() -> Path:
    """受管技能库路径（不创建）。"""
    return data_dir() / MANAGED_DIRNAME


def bundled_skills_dir() -> Path:
    """随包发布的内置技能目录（`app/skills/bundled/`，不创建）。

    刻意放在 Python 包内而非 `data/`：根 `.gitignore` 第 2 行忽略 `backend/data/`，
    放那里技能不会进版本库、装机即丢；包内目录随代码一起提交与分发。
    """
    return Path(__file__).resolve().parent / BUNDLED_DIRNAME


# ── 内置技能播种 ──────────────────────────────────────────────────────────

def _bundled_manifest_path(managed: Path) -> Path:
    return managed / BUNDLED_MANIFEST


def _read_bundled_manifest(managed: Path) -> dict:
    """读播种清单 → `{"version": int|None, "seeded": {身份名: {"dir": 目录名, "digest": 内容摘要}}}`。

    兼容旧版清单（`seeded` 是纯名字列表）：目录名即身份名、`digest` 为空串 → 这些
    记录不会参与版本刷新（见 `seed_bundled`），但仍参与「尊重删除」。
    """
    data = _read_json(_bundled_manifest_path(managed))
    raw = data.get("seeded")
    seeded: dict[str, dict] = {}
    if isinstance(raw, dict):
        for name, rec in raw.items():
            rec = rec if isinstance(rec, dict) else {}
            seeded[str(name)] = {
                "dir": str(rec.get("dir") or ""),
                "digest": str(rec.get("digest") or ""),
            }
    elif isinstance(raw, list):
        for name in raw:
            n = str(name)
            seeded[n] = {"dir": n, "digest": ""}
    version = data.get("version")
    return {
        "version": int(version) if isinstance(version, int) else None,
        "seeded": seeded,
    }


def _skill_digest(path: Path) -> str:
    """技能目录内容摘要（相对路径 + 字节内容），用于判断用户是否改动过内置技能。"""
    h = hashlib.sha256()
    try:
        files = sorted(p for p in path.rglob("*") if p.is_file())
    except OSError:  # pragma: no cover - 权限/并发删除
        return ""
    for f in files:
        h.update(str(f.relative_to(path)).replace("\\", "/").encode("utf-8"))
        h.update(b"\0")
        try:
            h.update(f.read_bytes())
        except OSError:  # pragma: no cover
            return ""
        h.update(b"\0")
    return h.hexdigest()


def _frontmatter_name(skill_md: Path, fallback: str) -> str:
    """读技能 frontmatter 的 `name`（缺失/损坏则回退目录名）——与 loader 同一身份规则。"""
    try:
        from .loader import _split_frontmatter  # 局部导入避免循环依赖
        meta, _body = _split_frontmatter(skill_md.read_text(encoding="utf-8"))
        name = str(meta.get("name") or "").strip()
        if name:
            return name
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to read skill name from %s: %s", skill_md, e)
    return fallback


def _discover_bundled() -> dict[str, Path]:
    """扫描内置目录，返回 `{技能身份名: 技能源目录}`（含 SKILL.md 的目录）。

    **身份名 = frontmatter `name`**，缺失时回退目录名 —— 与 `SkillLoader._load_skill_file`
    的解析规则一致（loader 的 `name` 优先、目录名兜底）。播种清单因此能直接与
    `SkillLoader._skills` 的键空间对齐，frontmatter name 与目录名不同的内置技能也不会
    丢失「内置」徽章（目录名只用于定位目录）。
    """
    root = bundled_skills_dir()
    out: dict[str, Path] = {}
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if "SKILL.md" in filenames:
            p = Path(dirpath)
            name = _frontmatter_name(p / "SKILL.md", p.name)
            if name not in out:      # 靠前的目录优先，避免覆盖
                out[name] = p
    return out


def _replace_tree(src: Path, dst: Path) -> None:
    """用 `src` 覆盖 `dst`（先落到同盘临时目录再整体替换，避免中途失败留半份内容）。"""
    tmp = Path(tempfile.mkdtemp(prefix=".bundled-", dir=str(dst.parent)))
    staged = tmp / dst.name
    try:
        shutil.copytree(src, staged)
        if dst.exists():
            shutil.rmtree(dst)
        staged.replace(dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def seed_bundled(managed: str | Path | None = None) -> list[str]:
    """把内置技能播种进受管库，返回本次**新播种**的技能名列表。

    三个关键不变量：
    - **幂等**：清单里有记录的技能永远跳过，重复启动零拷贝。
    - **非破坏**：受管库里已存在的同名目录绝不覆盖 —— 用户改过的内置技能不会被
      升级冲掉；外部源/自建的同名技能也不受影响。
    - **尊重删除**：用户删掉的技能不复活（清单记录即视为已处理）。

    **记账规则（A2）**：只有「本次确实从包内复制过去」的技能才写进清单。受管库里
    事先存在的同名目录（用户自建）**不记账、不覆盖、也不打「内置」徽章** —— 否则用户
    自己写的 `tdd/` 会被谎称成随包内置。

    `BUNDLED_VERSION` 递增（A3）→ 触发**未改动技能的内容刷新**：清单里带摘要且
    受管副本仍与上次播种时一致（即用户没改过）的技能会被上游新内容覆盖；用户改过的
    一律不动。新增技能任何版本下都会补种。
    """
    managed_dir = Path(managed) if managed is not None else managed_skills_dir()
    bundled = _discover_bundled()
    if not bundled:
        return []
    try:
        managed_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:  # noqa: BLE001
        logger.warning("Failed to create managed skills dir for seeding: %s", e)
        return []

    manifest = _read_bundled_manifest(managed_dir)
    seeded: dict[str, dict] = dict(manifest["seeded"])
    version_changed = manifest["version"] != BUNDLED_VERSION
    newly: list[str] = []
    refreshed: list[str] = []
    for name, src in sorted(bundled.items()):
        dst = managed_dir / src.name          # 目录名只用于定位，身份键是 name
        rec = seeded.get(name)
        if rec is not None:
            # 幂等：已处理过。仅在版本变化且用户未改动时同步上游内容。
            if not version_changed:
                continue
            recorded = rec.get("digest") or ""
            if not recorded or not dst.is_dir():
                continue
            if _skill_digest(dst) != recorded:
                continue                      # 用户改过 → 绝不覆盖
            try:
                upstream = _skill_digest(src)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to digest bundled skill %s: %s", name, e)
                continue
            if upstream == recorded:
                continue                      # 上游内容没变
            try:
                _replace_tree(src, dst)
                rec["dir"] = src.name
                rec["digest"] = upstream
                refreshed.append(name)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to refresh bundled skill %s: %s", name, e)
            continue
        if dst.exists():
            # 用户自建/改过的同名目录：不覆盖，**也不记账**（不标「内置」）。
            logger.info("Skipped bundled skill %s: %s already exists (not marked bundled)", name, dst)
            continue
        try:
            shutil.copytree(src, dst)
            seeded[name] = {"dir": src.name, "digest": _skill_digest(dst)}
            newly.append(name)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to seed bundled skill %s: %s", name, e)

    if newly or refreshed or version_changed:
        try:
            _bundled_manifest_path(managed_dir).write_text(
                json.dumps(
                    {
                        "version": BUNDLED_VERSION,
                        "seeded": {n: seeded[n] for n in sorted(seeded)},
                    },
                    indent=2, ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to write bundled skills manifest: %s", e)
    if newly or refreshed:
        logger.info("Seeded %d / refreshed %d bundled skills in %s",
                    len(newly), len(refreshed), managed_dir)
    return newly


def bundled_names(managed: str | Path | None = None) -> set[str]:
    """受管库中由内置播种而来的技能**身份名**集合（供 UI 打「内置」徽章）。

    键与 `SkillLoader._skills` 同源（frontmatter `name`），loader 用它给
    `Skill.bundled` 打标。
    """
    managed_dir = Path(managed) if managed is not None else managed_skills_dir()
    return set(_read_bundled_manifest(managed_dir)["seeded"])


def _sources_path() -> Path:
    return backend_dir() / SOURCES_FILE


def _read_json(path: Path) -> dict:
    try:
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to read skills sources from %s: %s", path, e)
    return {}


def load_sources() -> tuple[str, list[str]]:
    """返回 `(managed_dir, extra_dirs)`。

    - managed 缺失/损坏 → 回退到 `data/skills`（由调用方 create）。
    - 旧格式 `runtime_skills_dir.json` → 其 `directory` 迁移为第一个外部源。
    - `backend/skills` 若存在 → 也作为外部源（兼容早期手工放置的技能）。
    """
    managed = str(managed_skills_dir())
    extras: list[str] = []

    legacy_file = backend_dir() / LEGACY_SOURCES_FILE
    legacy_dir = str(_read_json(legacy_file).get("directory", "").strip() or "")
    if legacy_dir:
        extras.append(legacy_dir)
        logger.info("Migrated legacy skills dir to external source: %s", legacy_dir)

    data = _read_json(_sources_path())
    m = str(data.get("managed", "").strip() or "")
    if m:
        managed = m
    for d in data.get("extra_dirs", []) or []:
        d = str(d).strip()
        if d and d not in extras:
            extras.append(d)

    old_default = str(backend_dir() / LEGACY_FALLBACK_DIRNAME)
    if old_default not in extras and Path(old_default).is_dir():
        extras.append(old_default)

    return managed, _dedupe(extras, exclude=managed)


def save_sources(managed: str, extra_dirs: list[str]) -> None:
    """落盘状态；顺手删除已迁移完的旧版文件（幂等）。"""
    p = _sources_path()
    clean = _dedupe(extra_dirs, exclude=managed)  # 受管库绝不能出现在外部源里（否则被扫两遍）
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"managed": managed, "extra_dirs": clean},
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to save skills sources to %s: %s", p, e)
    legacy = backend_dir() / LEGACY_SOURCES_FILE
    try:
        if legacy.exists():
            legacy.unlink()
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to remove legacy skills dir file %s: %s", legacy, e)


def _key(d: str) -> str:
    try:
        return str(Path(d).expanduser().resolve())
    except OSError:  # pragma: no cover
        return str(d)


def _dedupe(dirs: list[str], exclude: str = "") -> list[str]:
    """去重并剔除 `exclude`（受管库本身），保留首次出现的原始写法。"""
    banned = _key(exclude) if exclude else ""
    out: list[str] = []
    seen: set[str] = set()
    for d in dirs:
        k = _key(d)
        if k in seen or (banned and k == banned):
            continue
        seen.add(k)
        out.append(d)
    return out
