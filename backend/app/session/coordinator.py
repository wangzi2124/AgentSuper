"""Web Chat v2 execution coordinator (Phase 2).

单会话串行执行：prompt admission 后唤醒；逐条投递 inbox item。
Phase 2 先复用 PlatformContext.run（TurnHarness + LangGraph），事件总线在 Phase 3。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class ExecutionCoordinator:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        # session_id -> "stop" | "resume"：只记录真正打断的回合。
        self._interrupt: dict[str, str] = {}
        # 每次 wake 自增；drain 退出前对比，避免「空队列检查 → 任务注销」窗口丢唤醒。
        self._wake_seq: dict[str, int] = {}
        self._closed = False
        self._lock = asyncio.Lock()

    def is_running(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return bool(task and not task.done())

    async def wake(self, session_id: str) -> None:
        async with self._lock:
            if self._closed:
                return
            self._wake_seq[session_id] = self._wake_seq.get(session_id, 0) + 1
            task = self._tasks.get(session_id)
            if task is not None and not task.done():
                return
            self._tasks[session_id] = asyncio.create_task(self._drain(session_id))

    def interrupt(self, session_id: str, resume: bool = False) -> bool:
        if not self.ctx.cancel_turn(session_id):
            return False
        # 空闲时 interrupt 不留下标记，否则下一次成功回合会被误报为 interrupted。
        self._interrupt[session_id] = "resume" if resume else "stop"
        return True

    async def wait(self, session_id: str) -> None:
        task = self._tasks.get(session_id)
        if task is not None:
            await asyncio.shield(task)

    async def close(self) -> None:
        self._closed = True
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _drain(self, session_id: str) -> None:
        from . import repository as repo
        from app.events.emitter import emit_event

        seen = self._wake_seq.get(session_id, 0)
        interrupted = False
        try:
            while True:
                item = repo.promote_next(session_id)
                if item is None:
                    # 与 wake/任务注销共用一个临界区：若期间有新唤醒，继续 drain 而不是退出。
                    async with self._lock:
                        current = self._wake_seq.get(session_id, 0)
                        if current != seen:
                            seen = current
                            continue
                    break
                await self._run_item(session_id, item)
                mode = self._interrupt.get(session_id)
                if mode is None:
                    continue
                interrupted = True
                if mode == "stop":
                    break
                # resume=true 只继续 steer/control，queue 保持排队。
                if not any(
                    i.get("delivery") == "steer" for i in repo.list_inputs(session_id)
                ):
                    break
        except asyncio.CancelledError:
            await self._reset_idle(session_id)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("v2 execution failed: %s", session_id, exc_info=True)
            await emit_event(
                self.ctx,
                "session.execution.failed",
                {
                    "session_id": session_id,
                    "error": {"type": "execution_error", "message": str(exc)},
                },
                session_id=session_id,
            )
            await self._finish(session_id, "interrupted" if interrupted else "failed")
        else:
            outcome = "interrupted" if interrupted else "succeeded"
            await emit_event(
                self.ctx,
                "session.execution.interrupted" if interrupted else "session.execution.succeeded",
                (
                    {"session_id": session_id, "reason": "user"}
                    if interrupted
                    else {"session_id": session_id}
                ),
                session_id=session_id,
            )
            await self._finish(session_id, outcome)
        finally:
            self._interrupt.pop(session_id, None)
            async with self._lock:
                current = self._tasks.get(session_id)
                if current is asyncio.current_task():
                    self._tasks.pop(session_id, None)
                    if (
                        not self._closed
                        and self._wake_seq.get(session_id, 0) != seen
                    ):
                        # 退出检查后又有新入队/唤醒：立刻接管，避免任务注销留下孤儿条目。
                        self._tasks[session_id] = asyncio.create_task(
                            self._drain(session_id)
                        )

    async def _reset_idle(self, session_id: str) -> None:
        """取消（shutdown/close）时把会话状态归零，避免永久卡在 busy。"""
        try:
            from . import repository as repo

            repo.set_session_status(session_id, "idle", outcome="interrupted")
            self.ctx.sessions.update(session_id, status="idle")
        except Exception:  # noqa: BLE001
            logger.warning("重置会话状态失败: %s", session_id, exc_info=True)

    async def _finish(self, session_id: str, outcome: str) -> None:
        from . import repository as repo
        from app.events.emitter import emit_event

        repo.set_session_status(session_id, "idle", outcome=outcome)
        self.ctx.sessions.update(session_id, status="idle")
        await emit_event(
            self.ctx,
            "session.idle",
            {"session_id": session_id, "outcome": outcome},
            session_id=session_id,
        )
        await emit_event(
            self.ctx,
            "session.status",
            {"session_id": session_id, "status": {"type": "idle"}},
            session_id=session_id,
        )

    async def _run_item(self, session_id: str, item: dict[str, Any]) -> None:
        from . import repository as repo
        from app.events.emitter import emit_event
        from app.session.context import set_session_context

        set_session_context(session_id, self.ctx)

        repo.set_session_status(session_id, "busy")
        self.ctx.sessions.update(session_id, status="busy")
        await emit_event(
            self.ctx,
            "session.execution.started",
            {"session_id": session_id},
            session_id=session_id,
        )
        await emit_event(
            self.ctx,
            "session.status",
            {"session_id": session_id, "status": {"type": "busy"}},
            session_id=session_id,
        )
        await emit_event(
            self.ctx,
            "session.inbox.delivered",
            {"session_id": session_id, "inbox_id": item["id"]},
            session_id=session_id,
        )
        if item.get("type") == "compaction":
            await emit_event(
                self.ctx,
                "session.compaction.started",
                {"session_id": session_id, "reason": "manual"},
                session_id=session_id,
            )
            try:
                result = await self.ctx.compact_session(session_id)
            except Exception as exc:  # noqa: BLE001
                await emit_event(
                    self.ctx,
                    "session.compaction.failed",
                    {
                        "session_id": session_id,
                        "reason": "manual",
                        "error": {"type": "compaction_error", "message": str(exc)},
                    },
                    session_id=session_id,
                )
                raise
            await emit_event(
                self.ctx,
                "session.compaction.ended",
                {
                    "session_id": session_id,
                    "reason": "manual",
                    "text": str((result or {}).get("summary") or ""),
                },
                session_id=session_id,
            )
            return
        payload = item.get("prompt") or {}
        question = str(payload.get("message") or payload.get("text") or "")
        files = payload.get("files")
        user_id = "anonymous"
        sess = self.ctx.sessions.get(session_id)
        if sess is not None:
            user_id = sess.user_id or "anonymous"
        assistant_id = f"{item.get('id') or 'assistant'}_assistant"
        from app.snapshot.vfs import snapshot_begin, snapshot_end

        snap_begin = snapshot_begin(self.ctx.vfs)
        await emit_event(
            self.ctx,
            "session.step.started",
            {
                "session_id": session_id,
                "assistant_message_id": assistant_id,
                "agent": None,
                "model": (
                    {"id": sess.model, "provider_id": "", "variant": None}
                    if sess is not None and sess.model
                    else None
                ),
                "started": int(time.time() * 1000),
            },
            session_id=session_id,
        )
        text_chunks: list[str] = []
        text_started = False
        before_usage = dict(getattr(sess, "tokens", None) or {})
        async for legacy in self.ctx.stream_turn_events(
            question,
            session_id=session_id,
            user_id=user_id,
            files=files if isinstance(files, list) else None,
            message_id=str(item.get("id") or ""),
            voice=payload.get("voice") if isinstance(payload.get("voice"), dict) else None,
        ):
            kind = legacy.get("type")
            if kind == "text_delta":
                delta = str(legacy.get("delta") or "")
                if not delta:
                    continue
                if not text_started:
                    text_started = True
                    await emit_event(
                        self.ctx,
                        "session.text.started",
                        {
                            "session_id": session_id,
                            "assistant_message_id": assistant_id,
                            "ordinal": 0,
                        },
                        session_id=session_id,
                    )
                text_chunks.append(delta)
                await emit_event(
                    self.ctx,
                    "session.text.delta",
                    {
                        "session_id": session_id,
                        "assistant_message_id": assistant_id,
                        "ordinal": 0,
                        "delta": delta,
                    },
                    session_id=session_id,
                )
            elif kind == "agent_step":
                step = legacy.get("step") or {}
                step_type = step.get("type")
                tool_name = str(step.get("tool_name") or step.get("name") or "tool")
                tool_id = str(step.get("step_id") or tool_name)
                if step_type == "tool_start":
                    await emit_event(
                        self.ctx,
                        "session.tool.called",
                        {
                            "session_id": session_id,
                            "assistant_message_id": assistant_id,
                            "id": tool_id,
                            "name": tool_name,
                            "input": step.get("tool_args") or {},
                            "executed": True,
                        },
                        session_id=session_id,
                    )
                elif step_type == "tool_end":
                    status = str(step.get("status") or "completed").lower()
                    result = step.get("tool_result")
                    content = [{"type": "text", "text": str(result or "")}]
                    if status in ("failed", "error"):
                        await emit_event(
                            self.ctx,
                            "session.tool.failed",
                            {
                                "session_id": session_id,
                                "assistant_message_id": assistant_id,
                                "id": tool_id,
                                "name": tool_name,
                                "error": {"type": "tool_error", "message": str(result or "tool failed")},
                                "content": content,
                                "metadata": {},
                                "executed": True,
                            },
                            session_id=session_id,
                        )
                    else:
                        await emit_event(
                            self.ctx,
                            "session.tool.success",
                            {
                                "session_id": session_id,
                                "assistant_message_id": assistant_id,
                                "id": tool_id,
                                "name": tool_name,
                                "content": content,
                                "metadata": {},
                                "executed": True,
                            },
                            session_id=session_id,
                        )
        final_state = await self.ctx.state(session_id)
        _files_changed, descriptor = snapshot_end(self.ctx.vfs, snap_begin)
        self.ctx.save_snapshot(session_id, assistant_id, descriptor)
        final = "".join(text_chunks) or str((final_state or {}).get("final_output") or "")
        if final and not text_started:
            # 短路径（寒暄/固定回复/非流式模型）没有 text_delta，直接补发开始+结束，
            # 否则前端在回合期间看不到任何 assistant 记录，只能等 hydration。
            await emit_event(
                self.ctx,
                "session.text.started",
                {
                    "session_id": session_id,
                    "assistant_message_id": assistant_id,
                    "ordinal": 0,
                },
                session_id=session_id,
            )
            text_started = True
        if text_started:
            await emit_event(
                self.ctx,
                "session.text.ended",
                {
                    "session_id": session_id,
                    "assistant_message_id": assistant_id,
                    "ordinal": 0,
                    "text": final,
                },
                session_id=session_id,
            )
        await emit_event(
            self.ctx,
            "session.step.ended",
            {
                "session_id": session_id,
                "assistant_message_id": assistant_id,
                "finish": "stop",
            },
            session_id=session_id,
        )
        if final:
            from langchain_core.messages import AIMessage

            msg = AIMessage(content=final, id=assistant_id)
            await self.ctx.graph.aupdate_state(
                {"configurable": {"thread_id": session_id}}, {"messages": [msg]}
            )
            await self.ctx.ensure_conversation(session_id)
        after_usage = (final_state or {}).get("token_usage") or {}
        from graph.usage import usage_delta

        usage = usage_delta(before_usage, after_usage)
        if usage:
            from graph.usage import merge_usage

            sess_row = self.ctx.sessions.get(session_id)
            if sess_row is not None:
                merged = merge_usage(sess_row.tokens or {}, usage)
                cost = float(sess_row.cost or 0.0) + float(usage.get("cost") or 0.0)
                self.ctx.sessions.update(session_id, tokens=merged, cost=round(cost, 8))
            await emit_event(
                self.ctx,
                "session.usage.updated",
                {
                    "session_id": session_id,
                    "cost": float(usage.get("cost") or 0.0),
                    "tokens": {
                        "input": int(usage.get("input") or 0),
                        "output": int(usage.get("output") or 0),
                        "reasoning": int(usage.get("reasoning") or 0),
                        "cache_read": int(usage.get("cache_read") or 0),
                        "cache_write": int(usage.get("cache_write") or 0),
                    },
                },
                session_id=session_id,
            )
