"""
opencode 风格全局存储目录(XDG 分层)。

对应 opencode packages/opencode/src/storage/storage.ts:
  data   -> 持久数据(上传文件、权限白名单、pinned_tools 等)
  cache  -> 可清理缓存(向量索引缓存、扫描缓存等)
  config -> 用户配置
  state  -> 运行状态(会话、项目命名空间)
  log    -> 日志
  bin    -> 下载的二进制/脚本

数据根解析优先级:
  1. ``agentsuper_data`` 显式设置（绝对路径原样；相对路径相对 backend/）
  2. 平台用户数据目录：Windows ``%LOCALAPPDATA%/agentsuper``、
     macOS ``~/Library/Application Support/agentsuper``、
     Linux ``$XDG_DATA_HOME/agentsuper``（缺省 ``~/.local/share/agentsuper``）

其余 5 个子目录默认跟随数据根（``<root>/cache`` 等），可分别用
``agentsuper_cache/config/state/log/bin`` 覆盖。相对路径一律基于数据根；
以 ``data/`` 开头的旧配置会自动去前缀并打 warning。

首次启动时，若数据根为空且无迁移标记，会把旧的 ``backend/data`` 全量拷贝
过来（源目录保留）。可用 ``agentsuper_migrate_legacy=false`` 关闭。
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

_APP_DIRNAME = "agentsuper"
_MIGRATE_MARKER = ".migrated-from-backend-data"
_MIGRATE_LOCK = ".migrate.lock"

# key -> (配置字段, 数据根下的默认子目录)
_SUBDIRS = {
    "cache": ("agentsuper_cache", "cache"),
    "config": ("agentsuper_config", "config"),
    "state": ("agentsuper_state", "state"),
    "log": ("agentsuper_log", "logs"),
    "bin": ("agentsuper_bin", "bin"),
}


def backend_dir() -> Path:
    """backend/ 根目录（storage/paths.py → parents[1]）。"""
    return Path(__file__).resolve().parents[1]


def _platform_data_root() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        home = Path(base) if base else Path.home() / "AppData" / "Local"
        return home / _APP_DIRNAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _APP_DIRNAME
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / _APP_DIRNAME


def data_root(base: Path | None = None) -> Path:
    """返回数据根（不创建目录）。``base`` 仅用于测试显式指定。"""
    if base is not None:
        return Path(base).expanduser()
    override = (settings.agentsuper_data or "").strip()
    if override:
        p = Path(override).expanduser()
        return p if p.is_absolute() else backend_dir() / p
    return _platform_data_root()


def _strip_legacy_prefix(raw: str, value: Path) -> Path:
    parts = value.parts
    if parts and parts[0] == "data" and len(parts) > 1:
        logger.warning(
            "配置路径 %r 使用了旧的 data/ 前缀；已按统一数据根解析为 %s",
            raw,
            Path(*parts[1:]),
        )
        return Path(*parts[1:])
    return value


def _resolve_under_root(value: str | None, default_name: str, root: Path) -> Path:
    raw = (value or "").strip() or default_name
    if not raw:
        return root
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return root / _strip_legacy_prefix(raw, p)


def data_path(value: str | None, default_name: str = "") -> Path:
    """把配置里的数据文件/目录路径解析到统一数据根。

    绝对路径原样返回；相对路径基于 ``data_root()``；旧的 ``data/`` 前缀会去掉。
    """
    return _resolve_under_root(value, default_name, data_root())


def _is_empty_dir(path: Path, ignore_names: frozenset[str] = frozenset()) -> bool:
    """目录是否为空（递归；空子目录不算内容，遇到文件立即返回 False）。"""
    try:
        for entry in path.iterdir():
            if entry.name in ignore_names:
                continue
            if entry.is_dir() and _is_empty_dir(entry):
                continue
            return False
    except FileNotFoundError:
        return True
    return True


def _migrate_legacy_data(root: Path) -> None:
    """首次启动把 backend/data 全量拷贝到空数据根（源保留，幂等）。"""
    if not bool(getattr(settings, "agentsuper_migrate_legacy", True)):
        return
    source = backend_dir() / "data"
    if not source.is_dir():
        return
    try:
        if root.resolve() == source.resolve():
            return
    except OSError:
        pass
    marker = root / _MIGRATE_MARKER
    if marker.exists():
        return
    reserved = frozenset({_MIGRATE_LOCK, _MIGRATE_MARKER})
    if root.exists() and not _is_empty_dir(root, reserved):
        logger.warning("数据根 %s 非空且无迁移标记，跳过旧数据自动拷贝", root)
        return

    root.mkdir(parents=True, exist_ok=True)
    lock = root / _MIGRATE_LOCK
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        logger.info("另一个进程正在迁移旧数据到 %s，跳过", root)
        return
    try:
        if marker.exists():
            return
        if not _is_empty_dir(root, reserved):
            logger.warning("数据根 %s 在迁移前已被占用，跳过旧数据自动拷贝", root)
            return
        logger.info("首次启动：拷贝旧数据 %s -> %s", source, root)
        shutil.copytree(source, root, dirs_exist_ok=True)
        marker.write_text(str(source), encoding="utf-8")
        logger.info("旧数据迁移完成（源目录保留）：%s", source)
    except Exception:  # noqa: BLE001 - 拷贝失败不应阻断启动，下次重试
        logger.warning("旧数据自动拷贝失败，将在下次启动重试", exc_info=True)
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def global_paths(base: Path | None = None) -> dict[str, Path]:
    """返回各用途目录并确保存在;环境变量可覆盖默认位置。"""
    root = data_root(base)
    if base is None:
        _migrate_legacy_data(root)
    paths = {"data": root}
    for key, (setting_name, dirname) in _SUBDIRS.items():
        value = (getattr(settings, setting_name, "") or "").strip()
        paths[key] = _resolve_under_root(value, dirname, root)
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def project_scoped(project_id: str) -> dict[str, Path]:
    """按 projectID 隔离的命名空间目录(对应 opencode 会话按 projectID 隔离)。

    返回 session / cache / log 三个子目录并确保存在。
    project_id 中的非法路径字符会被替换,防止目录穿越。
    """
    g = global_paths()
    safe_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in project_id)
    scoped = {
        "session": g["state"] / "projects" / safe_id / "sessions",
        "cache": g["cache"] / "projects" / safe_id,
        "log": g["log"] / "projects" / safe_id,
    }
    for p in scoped.values():
        p.mkdir(parents=True, exist_ok=True)
    return scoped
