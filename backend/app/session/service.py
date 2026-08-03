"""Session 业务服务：组合 repository + coordinator + history。

这是 /api/sessions 与现有 /api/chat/* 共用的门面。Agent 调用通过
`executor` 回调注入（chat.py 传入 RAGAgent / AgentBus 的执行体），
由 SessionCoordinator 保证 per-session 串行。
"""

import logging
from typing import Any, Awaitable, Callable, Optional

from . import history as session_history
from . import repository
from .coordinator import SessionCoordinator
from .models import ContextEpoch, Message, SessionInfo, SessionStatus

logger = logging.getLogger(__name__)

Executor = Callable[[str], Awaitable[None]]


class SessionService:
    """会话门面。

    executor(session_id)：该会话一次 drain 的执行体，内部：
    1. promote 下一条输入（steer 优先）
    2. 构建上下文（history.load + epoch）
    3. 调用 Agent，事件流经 SSE 透出
    4. 落库 user/assistant 消息
    """

    def __init__(self, executor: Optional[Executor] = None, global_limit: int = 2):
        self.coordinator = SessionCoordinator(executor or self._default_executor, global_limit=global_limit)

    # ── 生命周期 ─────────────────────────────────────────────────────────

    def create(
        self,
        user_id: str,
        *,
        project_id: Optional[str] = None,
        directory: str = "",
        parent_id: Optional[str] = None,
        agent: Optional[str] = None,
        model: Optional[Any] = None,
        kind: str = "chat",
        title: Optional[str] = None,
    ) -> SessionInfo:
        if parent_id:
            parent = repository.require_session(parent_id)
            project_id = project_id or parent.project_id
            directory = directory or parent.directory
        if not project_id:
            project = repository.resolve_project(directory or ".")
            project_id = project.id
        return repository.create_session(
            user_id, project_id, directory,
            parent_id=parent_id, agent=agent, model=model, kind=kind, title=title,
        )

    def get(self, user_id: str, session_id: str) -> SessionInfo:
        session = self._authorized(user_id, session_id)
        return session

    def list_sessions(
        self,
        user_id: str,
        *,
        project_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        roots_only: bool = False,
        search: Optional[str] = None,
        archived: bool = False,
        limit: int = 100,
    ) -> list[SessionInfo]:
        return repository.list_sessions(
            user_id, project_id=project_id, workspace_id=workspace_id,
            roots_only=roots_only, search=search, archived=archived, limit=limit,
        )

    def update(self, user_id: str, session_id: str, **fields: Any) -> SessionInfo:
        self._authorized(user_id, session_id)
        return repository.update_session(session_id, **fields)

    def remove(self, user_id: str, session_id: str) -> None:
        self._authorized(user_id, session_id)
        # 取消该会话及其子会话的后台执行（对齐 session.ts:remove）
        for sid in [session_id] + [c.id for c in repository.list_children(session_id)]:
            self.coordinator.cancel_best_effort(sid)
        repository.remove_session(session_id)

    def children(self, user_id: str, parent_id: str) -> list[SessionInfo]:
        self._authorized(user_id, parent_id)
        return repository.list_children(parent_id)

    def fork(self, user_id: str, session_id: str, message_id: Optional[str] = None) -> SessionInfo:
        """在指定消息（默认末尾）处克隆子会话，重建 parentID 映射。"""
        original = self._authorized(user_id, session_id)
        child = self.create(
            user_id,
            project_id=original.project_id,
            directory=original.directory,
            parent_id=session_id,
            agent=original.agent,
            model=original.model,
            kind=original.kind,
            title=f"{original.title} (fork)",
        )
        # 拷贝截至 message_id 的消息（含部件），此处为骨架占位。
        # TODO(P4): 遍历 repository.list_messages + list_parts，为新会话重放。
        return child

    # ── 消息 / 上下文 ────────────────────────────────────────────────────

    def append_message(self, user_id: str, session_id: str, msg_type: str, data: dict[str, Any]) -> Message:
        self._authorized(user_id, session_id)
        return repository.append_message(session_id, msg_type, data)

    def messages(self, user_id: str, session_id: str, after_seq: int = 0, limit: Optional[int] = None) -> list[Message]:
        self._authorized(user_id, session_id)
        return repository.list_messages(session_id, after_seq=after_seq, limit=limit)

    def context(self, user_id: str, session_id: str) -> dict[str, Any]:
        """模型视角上下文：epoch + 过滤后的历史（对齐 SessionRunner 的历史装载）。"""
        self._authorized(user_id, session_id)
        load = session_history.load(session_id)
        return {
            "session_id": session_id,
            "epoch": load.epoch.model_dump() if load.epoch else None,
            "history": [m.model_dump() for m in load.messages],
        }

    def initialize_context(self, session_id: str, baseline: str, snapshot: dict) -> ContextEpoch:
        return session_history.initialize_epoch(session_id, baseline, snapshot)

    def compact(self, user_id: str, session_id: str, checkpoint: str) -> None:
        """写压缩水位：落一条 compaction 消息并重建 epoch baseline。"""
        self._authorized(user_id, session_id)
        repository.append_message(session_id, "compaction", {"content": checkpoint})
        session_history.replace_epoch_after_compaction(session_id, checkpoint, {})

    # ── 输入 / 执行 ──────────────────────────────────────────────────────

    def prompt(self, user_id: str, session_id: str, text: str, files: Optional[list] = None,
               delivery: str = "steer") -> str:
        """投递输入（steer 打断 / queue 排队），并唤醒执行（对齐 opencode prompt+wake）。"""
        self._authorized(user_id, session_id)
        input_id = repository.admit_input(
            session_id,
            {"text": text, "files": files or []},
            delivery=delivery,
        )
        self.coordinator.wake(session_id)
        return input_id

    async def run(self, session_id: str) -> None:
        """显式启动 drain（对齐 SessionExecution.resume）。"""
        await self.coordinator.run(session_id)

    async def interrupt(self, user_id: str, session_id: str) -> None:
        self._authorized(user_id, session_id)
        await self.coordinator.interrupt(session_id)

    def status(self, user_id: str, session_id: str) -> SessionStatus:
        session = self._authorized(user_id, session_id)
        queue = repository.count_pending(session_id)
        return SessionStatus(
            session_id=session_id,
            status=session.status,
            queue_position=queue,
        )

    # ── 内部 ─────────────────────────────────────────────────────────────

    def _authorized(self, user_id: str, session_id: str) -> SessionInfo:
        session = repository.require_session(session_id)
        if session.user_id and session.user_id != user_id:
            raise repository.Forbidden(session_id)
        return session

    async def _default_executor(self, session_id: str) -> None:
        """骨架默认执行体：promote 输入 → 占位（由 chat.py 注入真实 Agent）。"""
        item = repository.promote_next(session_id)
        if not item:
            return
        logger.warning("SessionService._default_executor: no agent executor injected for %s", session_id)
