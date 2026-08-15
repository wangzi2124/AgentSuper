"""
opencode 风格文件系统层(后端 AgentSuper)。

设计对齐 opencode 的模块:
  - models.FileInfo / FileNode / FileContent   文件模型(core/file.ts)
  - fsutil.FSUtil                               纯路径助手与文件操作(core/fs-util.ts)
  - filesystem.FileSystem                       文件系统服务(core/filesystem.ts)
  - search / ripgrep                            搜索层(glob/grep/find)
  - shell.Shell                                 shell 识别(shell.ts)
  - watcher.FileSystemWatcher                   watcher(core/filesystem/watcher.ts)
  - project.Project                             (project/project.ts,按 projectID 隔离)

全局项目上下文由 app/runtime.py 初始化(set_project),业务代码通过
`from app.filesystem import get_project, normalize_path, find_up` 使用,
无需再自行拼接路径或猜测 git 根。
"""
from __future__ import annotations

from .core import ScanCache, contains, find_up, glob_up, normalize_path, overlaps, up
from .filesystem import Entry, FileSystem, LocationResolvingError, Match
from .fsutil import (
    DirEntry,
    contains as fsutil_contains,
    exists,
    ensure_dir,
    find_up as fsutil_find_up,
    glob_match,
    glob_up as fsutil_glob_up,
    is_dir,
    is_file,
    mime_type,
    normalize_path_pattern,
    normalize_path as fsutil_normalize_path,
    overlaps as fsutil_overlaps,
    read_directory_entries,
    read_file_string,
    read_json,
    resolve,
    up as fsutil_up,
    windows_path,
    write_json,
    write_with_dirs,
)
from .gitignore import GitignoreMatcher, glob_to_regex, parse_gitignore
from .models import FileContent, FileInfo, FileNode, FileStatus
from .project import Project
from .ripgrep import InvalidPatternError, RawMatch, find as rg_find, ripgrep_binary
from .search import Entry as SearchEntry, Match as SearchMatch
from .shell import acceptable as shell_acceptable, name as shell_name, platform_shell
from .watcher import Event as FileSystemEvent, FileSystemWatcher, has_native_binding

__all__ = [
    "DirEntry",
    "Entry",
    "FileContent",
    "FileInfo",
    "FileNode",
    "FileStatus",
    "FileSystem",
    "FileSystemEvent",
    "FileSystemWatcher",
    "GitignoreMatcher",
    "InvalidPatternError",
    "LocationResolvingError",
    "Match",
    "Project",
    "RawMatch",
    "ScanCache",
    "contains",
    "ensure_dir",
    "exists",
    "find_up",
    "fsutil_contains",
    "fsutil_find_up",
    "fsutil_glob_up",
    "fsutil_normalize_path",
    "fsutil_overlaps",
    "fsutil_up",
    "get_project",
    "glob_match",
    "glob_to_regex",
    "glob_up",
    "has_native_binding",
    "is_dir",
    "is_file",
    "mime_type",
    "normalize_path",
    "normalize_path_pattern",
    "overlaps",
    "parse_gitignore",
    "platform_shell",
    "read_directory_entries",
    "read_file_string",
    "read_json",
    "resolve",
    "rg_find",
    "ripgrep_binary",
    "set_project",
    "shell_acceptable",
    "shell_name",
    "up",
    "windows_path",
    "write_json",
    "write_with_dirs",
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
