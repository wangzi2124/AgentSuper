"""
opencode 风格文件系统层(后端 AgentSuper)。

设计对齐 opencode 的三个关键模块:
  - models.FileInfo / FileNode / FileContent  文件模型(file/index.ts)
  - core 路径工具与扫描缓存                     (util/filesystem.ts + file/index.ts)
  - project.Project                            (project/project.ts,按 projectID 隔离)

全局项目上下文由 app/runtime.py 初始化(set_project),业务代码通过
`from app.filesystem import get_project, normalize_path, find_up` 使用,
无需再自行拼接路径或猜测 git 根。
"""
from __future__ import annotations

from .core import ScanCache, contains, find_up, glob_up, normalize_path, overlaps, up
from .gitignore import GitignoreMatcher, glob_to_regex, parse_gitignore
from .models import FileContent, FileInfo, FileNode, FileStatus
from .project import Project

__all__ = [
    "ScanCache",
    "FileContent",
    "FileInfo",
    "FileNode",
    "FileStatus",
    "GitignoreMatcher",
    "Project",
    "contains",
    "find_up",
    "glob_to_regex",
    "glob_up",
    "get_project",
    "normalize_path",
    "overlaps",
    "parse_gitignore",
    "set_project",
    "up",
]

_project: Project | None = None


def set_project(project: Project) -> None:
    """设置全局项目上下文(由 runtime 初始化时调用)。"""
    global _project
    _project = project


def get_project() -> Project:
    """获取全局项目上下文;未初始化时按当前目录惰性发现。"""
    global _project
    if _project is None:
        _project = Project.from_directory()
    return _project
