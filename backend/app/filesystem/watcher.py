"""
FileSystemWatcher — opencode @opencode-ai/core/filesystem/watcher 移植。

对齐 packages/core/src/filesystem/watcher.ts：
  - Event.Updated / Event.Edited 事件类型
  - SUBSCRIBE_TIMEOUT_MS 超时
  - hasNativeBinding / 降级语义

实现策略（[P2 #10]）：
  1. **原生优先**：若 `watchdog` 已安装，`start()` 使用其原生事件监听
     （Windows ReadDirectoryChangesW / Linux inotify / macOS FSEvents），
     实时、无轮询、几乎零 CPU。
  2. **回退**：未安装 watchdog（或 observer 启动失败）时，回退到
     mtime 轮询（`poll()`），无需第三方依赖。
  3. 检测能力不足时按 opencode 语义静默降级(不抛错)。

`poll()` 始终可用（基于 mtime 快照差分），便于同步/可测试的检测；
`has_native_binding()` 反映当前环境是否具备原生监听能力。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

SUBSCRIBE_TIMEOUT_MS = 10_000

try:
    from watchdog.events import FileSystemEventHandler as _FSHandler
    from watchdog.observers import Observer as _Observer

    _WATCHDOG_AVAILABLE = True
except Exception:  # pragma: no cover - 依赖缺失时降级
    _FSHandler = object  # type: ignore[assignment,misc]
    _Observer = None  # type: ignore[assignment]
    _WATCHDOG_AVAILABLE = False


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
    """是否具备原生文件监听能力(已安装 watchdog → True)。"""
    return _WATCHDOG_AVAILABLE


if _WATCHDOG_AVAILABLE:

    class _WatchdogHandler(_FSHandler):  # type: ignore[misc,valid-type]
        """将 watchdog 原生事件映射为 WatchUpdate 并分发给 owner 的回调。"""

        def __init__(self, owner: "FileSystemWatcher") -> None:
            super().__init__()
            self._owner = owner

        def _emit(self, operation: str, path: str) -> None:
            try:
                self._owner._dispatch(WatchUpdate(Event.Updated, str(path), operation))
            except Exception:
                pass

        def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
            self._emit("create", event.src_path)

        def on_modified(self, event) -> None:  # type: ignore[no-untyped-def]
            self._emit("update", event.src_path)

        def on_deleted(self, event) -> None:  # type: ignore[no-untyped-def]
            self._emit("delete", event.src_path)

        def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
            self._emit("delete", event.src_path)
            self._emit("create", getattr(event, "dest_path", event.src_path))

else:
    _WatchdogHandler = None  # type: ignore[assignment]


class FileSystemWatcher:
    """文件系统 watcher：原生(watchdog)优先，回退 mtime 轮询。

    用法:
        watcher = FileSystemWatcher("backend/")
        def on_update(ev: WatchUpdate): ...
        watcher.subscribe(on_update)
        watcher.poll()    # 手动触发一次基于快照的检测
        watcher.start()   # 后台监听(原生优先,否则轮询线程)
        watcher.close()
    """

    def __init__(self, root: str, interval_ms: int = 1000) -> None:
        self.root = Path(root).resolve()
        self.interval = max(200, interval_ms)
        self._callbacks: list[Callable[[WatchUpdate], None]] = []
        self._snapshots: dict[str, tuple[int, int]] = {}
        self._closed = False
        self._observer: Optional[object] = None
        self._thread: Optional[object] = None

    def subscribe(self, cb: Callable[[WatchUpdate], None]) -> None:
        """注册更新回调。"""
        self._callbacks.append(cb)

    def _dispatch(self, ev: WatchUpdate) -> None:
        """将事件投递给所有回调(单个回调异常不影响其他回调)。"""
        for cb in list(self._callbacks):
            try:
                cb(ev)
            except Exception:
                pass

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
        """执行一次基于快照的扫描,触发回调并返回本次事件。"""
        snap = self._snapshot()
        events = self._detect(self._snapshots, snap)
        self._snapshots = snap
        for ev in events:
            self._dispatch(ev)
        return events

    def start(self) -> None:
        """启动后台监听:原生(watchdog)优先,否则回退 mtime 轮询线程。"""
        self._closed = False

        if _WATCHDOG_AVAILABLE:
            try:
                observer = _Observer()  # type: ignore[misc]
                observer.schedule(_WatchdogHandler(self), str(self.root), recursive=True)
                observer.start()
                self._observer = observer
                return
            except Exception:
                # 原生监听不可用 → 降级为轮询
                self._observer = None

        import threading

        def _loop() -> None:
            while not self._closed:
                try:
                    self.poll()
                except Exception:
                    pass
                time.sleep(self.interval / 1000.0)

        self._thread = threading.Thread(target=_loop, name="fswatcher", daemon=True)
        self._thread.start()  # type: ignore[union-attr]

    def close(self) -> None:
        self._closed = True
        if self._observer is not None:
            try:
                self._observer.stop()  # type: ignore[attr-defined]
                self._observer.join(timeout=2)  # type: ignore[attr-defined]
            except Exception:
                pass
            self._observer = None
