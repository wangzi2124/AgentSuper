"""导入冒烟：文件系统服务层删除后，包与 app 入口仍可导入。

锁定的回归：曾删除 `app/filesystem/search.py` 却漏改
`app/filesystem/filesystem.py` 的 `from . import search`，导致只在
uvicorn 启动期才暴露 ImportError。
"""

from __future__ import annotations

import importlib
import importlib.util


def test_filesystem_package_imports() -> None:
    fs = importlib.import_module("app.filesystem")
    for name in ("Project", "ScanCache", "set_project", "get_project", "ripgrep_binary"):
        assert hasattr(fs, name), name


def test_filesystem_service_layer_removed() -> None:
    fs = importlib.import_module("app.filesystem")
    for name in ("FileSystem", "Entry", "Match", "LocationResolvingError"):
        assert not hasattr(fs, name), name
    assert importlib.util.find_spec("app.filesystem.filesystem") is None
    assert importlib.util.find_spec("app.filesystem.search") is None


def test_app_main_imports() -> None:
    assert importlib.import_module("app.main") is not None
