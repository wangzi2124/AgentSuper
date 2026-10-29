"""
opencode 风格全局存储目录(XDG 分层)。

对应 opencode packages/opencode/src/storage/storage.ts:
  data   -> 持久数据(上传文件、权限白名单、pinned_tools 等)
  cache  -> 可清理缓存(向量索引缓存、扫描缓存等)
  config -> 用户配置
  state  -> 运行状态(会话、项目命名空间)
  log    -> 日志
  bin    -> 下载的二进制/脚本

配置可整体覆盖(未设置时默认全部位于 backend/ 下):
  agentsuper_data / agentsuper_cache / agentsuper_config /
  agentsuper_state / agentsuper_log / agentsuper_bin
"""
from __future__ import annotations

from pathlib import Path

from config import settings

_BASE = Path(__file__).resolve().parents[1]  # backend/

_SETTING_KEYS = {
    "data": "agentsuper_data",
    "cache": "agentsuper_cache",
    "config": "agentsuper_config",
    "state": "agentsuper_state",
    "log": "agentsuper_log",
    "bin": "agentsuper_bin",
}


def _defaults(base: Path) -> dict[str, Path]:
    return {
        "data": base / "data",
        "cache": base / "data" / "cache",
        "config": base / "data" / "config",
        "state": base / "data" / "state",
        "log": base / "data" / "logs",
        "bin": base / "data" / "bin",
    }


def global_paths(base: Path | None = None) -> dict[str, Path]:
    """返回各用途目录并确保存在;环境变量可覆盖默认位置。"""
    root = Path(base) if base is not None else _BASE
    paths = _defaults(root)
    for key, setting_name in _SETTING_KEYS.items():
        value = getattr(settings, setting_name, "") or ""
        if value:
            paths[key] = Path(value).expanduser()
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
