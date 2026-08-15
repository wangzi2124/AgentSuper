"""
FileSystemWatcher — opencode @opencode-ai/core/filesystem/watcher 移植。

对齐 packages/core/src/filesystem/watcher.ts：
  - Event.Updated / Event.Edited 事件类型
  - SUBSCRIBE_TIMEOUT_MS 超时
  - hasNativeBinding / 降级语义

AgentSuper 为请求驱动的后端(无 @parcel/watcher),因此提供轻量轮询 watcher:
subscribe() 返回生成器/回调注册,基于 mtime 变化检测文件更新;无需第三方依赖
(Watchdog 未安装),检测能力不足时按 opencode 语义静默降级(不抛错)。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

SUBSCRIBE_TIMEOUT_MS = 10_000


class Event(str, Enum):
    """文件系统事件类型,对应 opencode FileSystemWatcher.Event。"""

    Updated = "updated"
    Edited = "edited"


@dataclass
class WatchUpdate:
    """一次 watch 回调的事件负载。"""

    type: Event
    path: str
    operation: str = "update"  # "create" | "update" | "delete"


def has_native_binding() -> bool:
    """是否可用的原生文件监听能力(本环境无 @parcel/watcher/watchdog,返回 False)。"""
    return False


class FileSystemWatcher:
    """基于 mtime 轮询的轻量 watcher。

    用法:
        watcher = FileSystemWatcher("backend/")
        def on_update(ev: WatchUpdate): ...
        watcher.subscribe(on_update)
        watcher.poll()   # 手动触发一次检测
        watcher.close()
    """

    def __init__(self, root: str, interval_ms: int = 1000) -> None:
        self.root = Path(root).resolve()
        self.interval = max(200, interval_ms)
        self._callbacks: list[Callable[[WatchUpdate], None]] = []
        self._snapshots: dict[str, tuple[int, int]] = {}
        self._closed = False

    def subscribe(self, cb: Callable[[WatchUpdate], None]) -> None:
        """注册更新回调。"""
        self._callbacks.append(cb)

    def _snapshot(self) -> dict[str, tuple[int, int]]:
        """递归快照:path -> (mtime_ns, size)。忽略无法访问的路径。"""
        snap: dict[str, tuple[int, int]] = {}
        for p in self.root.rglob("*"):
            try:
                st = p.stat()
            except OSError:
                continue
            snap[str(p)] = (st.st_mtime_ns, st.st_size)
        return snap

    def _detect(self, old: dict[str, tuple[int, int]], new: dict[str, tuple[int, int]]) -> list[WatchUpdate]:
        events: list[WatchUpdate] = []
        for path, meta in new.items():
            if path not in old:
                events.append(WatchUpdate(Event.Updated, path, operation="create"))
            elif old[path] != meta:
                events.append(WatchUpdate(Event.Updated, path, operation="update"))
        for path in old:
            if path not in new:
                events.append(WatchUpdate(Event.Updated, path, operation="delete"))
        return events

    def poll(self) -> list[WatchUpdate]:
        """执行一次扫描,触发回调并返回本次事件。"""
        snap = self._snapshot()
        events = self._detect(self._snapshots, snap)
        self._snapshots = snap
        for ev in events:
            for cb in self._callbacks:
                try:
                    cb(ev)
                except Exception:
                    pass
        return events

    def start(self) -> None:
        """后台线程轮询(降级模式,资源有限)。"""
        import threading

        self._closed = False

        def _loop() -> None:
            while not self._closed:
                try:
                    self.poll()
                except Exception:
                    pass
                time.sleep(self.interval / 1000.0)

        self._thread = threading.Thread(target=_loop, name="fswatcher", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._closed = True
