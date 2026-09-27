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
"""

from __future__ import annotations

import json
import logging
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


def backend_dir() -> Path:
    """backend/ 根目录（app/skills/registry.py → parents[2]）。"""
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return backend_dir() / "data"


def managed_skills_dir() -> Path:
    """受管技能库路径（不创建）。"""
    return data_dir() / MANAGED_DIRNAME


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
