"""
opencode 风格 Project 模型。

对应 opencode packages/opencode/src/project/project.ts:
  - 从任意目录向上查找 .git 确定 worktree(git root)
  - 项目 ID 优先取 git 根提交哈希,持久化到 <worktree>/.git/opencode/project.json
  - 会话 / 缓存 / 日志按 projectID 命名空间隔离(见 app/storage/paths.py)
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .core import find_up, normalize_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Project:
    """项目标识:worktree(绝对路径)+ id(项目 ID)。"""

    worktree: str
    id: str

    @property
    def root(self) -> Path:
        return Path(self.worktree)

    def is_inside(self, path: str | Path) -> bool:
        """判断路径是否位于本项目 worktree 之内(对应 opencode project.isInside)。"""
        return Path(normalize_path(path)).is_relative_to(Path(self.worktree))

    def relative(self, path: str | Path) -> str:
        """返回路径相对 worktree 的 POSIX 风格路径(用于会话记录/文件树展示)。"""
        return Path(normalize_path(path)).relative_to(Path(self.worktree)).as_posix()

    @classmethod
    def from_directory(cls, directory: str | Path | None = None) -> "Project":
        """从目录向上查找 .git 确定 worktree;找不到则用目录自身作为 worktree。"""
        start = Path(normalize_path(directory) if directory else Path.cwd())
        git_dir = find_up(".git", start)
        root = str(Path(git_dir).parent) if git_dir is not None else str(start)
        return cls(worktree=root, id=_project_id(root))

    @classmethod
    def from_git(cls, directory: str | Path | None = None) -> "Project":
        """优先用 `git rev-parse --show-toplevel` 精确获取 worktree(处理 submodule/嵌套)。

        git 不可用时降级为 from_directory 的向上查找。
        """
        start = str(Path(directory).resolve() if directory else Path.cwd())
        try:
            out = subprocess.run(
                ["git", "-C", start, "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip():
                root = normalize_path(out.stdout.strip())
                return cls(worktree=root, id=_project_id(root))
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
            logger.debug("git rev-parse failed, fallback to find_up for %s", start)
        return cls.from_directory(start)


def _project_id(worktree: str) -> str:
    """项目 ID:优先 git 根提交哈希,否则用规范化路径的 sha1 前缀。

    持久化到 <worktree>/.git/opencode/project.json,使同一仓库跨会话 ID 稳定。
    无 .git 的目录不持久化,每次按路径哈希生成(结果仍稳定)。
    """
    root = Path(worktree)
    meta = root / ".git" / "opencode" / "project.json"
    if meta.exists():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            if data.get("id"):
                return str(data["id"])
        except (OSError, json.JSONDecodeError):
            pass

    pid = ""
    try:
        out = subprocess.run(
            ["git", "-C", worktree, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            pid = "git:" + out.stdout.strip()[:12]
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        pass
    if not pid:
        digest = hashlib.sha1(normalize_path(worktree).encode("utf-8")).hexdigest()[:16]
        pid = "path:" + digest

    try:
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(
            json.dumps({"id": pid, "worktree": worktree}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("cannot persist project id to %s (non-git dir or read-only)", meta)
    return pid
