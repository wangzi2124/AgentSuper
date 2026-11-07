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
        self._cancelled: set[str] = set()
        self._lock = asyncio.Lock()

    def is_running(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return bool(task and not task.done())

    async def wake(self, session_id: str) -> None:
        async with self._lock:
            task = self._tasks.get(session_id)
            if task is not None and not task.done():
                return
            self._tasks[session_id] = asyncio.create_task(self._drain(session_id))

    def interrupt(self, session_id: str) -> bool:
        self._cancelled.add(session_id)
        return bool(self.ctx.cancel_turn(session_id))

    async def wait(self, session_id: str) -> None:
        task = self._tasks.get(session_id)
        if task is not None:
            await asyncio.shield(task)

    async def close(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _drain(self, session_id: str) -> None:
        from . import repository as repo
        from app.events.emitter import emit_event

        try:
            while True:
                item = repo.promote_next(session_id)
                if item is None:
                    break
                await self._run_item(session_id, item)
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
            interrupted = session_id in self._cancelled
            await self._finish(session_id, "interrupted" if interrupted else "failed")
        else:
            interrupted = session_id in self._cancelled
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
            self._cancelled.discard(session_id)
            async with self._lock:
                current = self._tasks.get(session_id)
                if current is asyncio.current_task():
                    self._tasks.pop(session_id, None)

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
