"""per-session 串行执行协调器。

对齐 opencode run-coordinator.ts 的语义：
- 同一 session 的输入串行执行（run 幂等 join 正在运行的相同 session）
- 不同 session 之间并行
- wake 合并多次唤醒（pendingWake 标志）
- interrupt 打断当前 fiber 并置 stopping

在全局并发上限（global_semaphore）约束下调度，替代现有 chat.py 的全局
`_agent_semaphore` 排队逻辑。
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


class SessionBusyError(Exception):
    pass


class SessionCoordinator:
    """以 session_id 为 key 的执行协调器。"""

    def __init__(self, executor: Callable[[str], Awaitable[None]], global_limit: int = 2):
        self._executor = executor
        self._global = asyncio.Semaphore(global_limit)
        self._active: dict[str, "_Entry"] = {}
        self._lock = asyncio.Lock()

    # ── 状态查询 ─────────────────────────────────────────────────────────

    @property
    def active_sessions(self) -> set[str]:
        return set(self._active.keys())

    def queue_position(self, session_id: str) -> int:
        """返回当前在该 session 队列中等待的输入数（对齐 opencode queue depth）。"""
        return self._active[session_id].pending if session_id in self._active else 0

    # ── 控制接口 ─────────────────────────────────────────────────────────

    async def run(self, session_id: str) -> None:
        """启动执行；若同 key 正在执行则 join，不重复启动（对齐 Coordinator.run）。"""
        async with self._lock:
            entry = self._active.get(session_id)
            if entry is not None:
                await entry.done
                return
            entry = _Entry()
            self._active[session_id] = entry
            task = asyncio.create_task(self._drive(session_id, entry, force=True))
            entry.owner = task

        try:
            await entry.done
        finally:
            pass

    def wake(self, session_id: str) -> None:
        """注册一次唤醒；若已有执行中的 entry，置 pendingWake 合并（对齐 wake）。

        需要在运行中的事件循环内调用；否则（同步上下文/测试/启动期）退化为
        仅置位标志，等待后续 async run() 驱动——此时队列已持久化在 DB 中，
        无执行中的 drain，no-op 语义正确。
        """
        async def _wake() -> None:
            async with self._lock:
                entry = self._active.get(session_id)
                if entry is not None:
                    entry.pendingWake = True
                    return
                entry = _Entry()
                self._active[session_id] = entry
                asyncio.create_task(self._drive(session_id, entry, force=False))
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            entry = self._active.get(session_id)
            if entry is not None:
                entry.pendingWake = True
            return
        asyncio.create_task(_wake())

    async def interrupt(self, session_id: str) -> None:
        """打断该 session 正在执行的任务（对齐 Coordinator.interrupt）。"""
        async with self._lock:
            entry = self._active.get(session_id)
            if entry is None or entry.owner is None:
                return
            entry.stopping = True
            entry.pendingWake = False
            entry.owner.cancel()
        try:
            await entry.done
        except asyncio.CancelledError:
            pass

    def cancel_best_effort(self, session_id: str) -> None:
        """同步尽力取消：不等待，用于会话删除等不可恢复场景。"""
        entry = self._active.get(session_id)
        if entry is None:
            return
        entry.stopping = True
        entry.pendingWake = False
        if entry.owner is not None:
            entry.owner.cancel()

    # ── 内部 ─────────────────────────────────────────────────────────────

    async def _drive(self, session_id: str, entry: "_Entry", force: bool) -> None:
        """单次 drain 循环：执行直到没有 pending 输入，或被打断。"""
        try:
            while True:
                if entry.stopping:
                    break
                async with self._global:
                    if entry.stopping:
                        break
                    await self._executor(session_id)
                if not entry.pendingWake:
                    break
                entry.pendingWake = False
        except asyncio.CancelledError:
            logger.info("coordinator: session %s interrupted", session_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("coordinator: session %s failed", session_id, exc_info=exc)
        finally:
            async with self._lock:
                if self._active.get(session_id) is entry:
                    self._active.pop(session_id, None)
            entry.done.set_result(None)


class _Entry:
    def __init__(self) -> None:
        self.done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.owner: Optional[asyncio.Task] = None
        self.pendingWake = False
        self.stopping = False
        self.pending = 0  # 排队计数（可选，由 service 维护）
