"""Session 业务服务：组合 repository + history。

这是 /api/sessions 与 /api/chat/* 共用的门面。多 Agent 请求经 AgentBus
（supervisor → 子 Agent）执行，端点侧直接落库；本服务负责会话 CRUD、
消息/上下文、压缩水位、撤销与级联取消，并保证同一会话的写操作串行。
"""

import asyncio
import logging
import time as tmod
from typing import Any, Optional

from . import history as session_history
from . import repository
from . import task_bridge
from .models import ContextEpoch, Message, SessionInfo, SessionStatus

logger = logging.getLogger(__name__)


class SessionService:
    """会话门面。"""

    def __init__(self):
        # 每会话写串行锁：multi-agent 直写、compact/revert/fork 共享，
        # 保证同一会话的消息追加与撤销不会交错（对齐"per-session 串行"承诺）。
        self._write_locks: dict[str, asyncio.Lock] = {}

    def write_lock(self, session_id: str) -> asyncio.Lock:
        """获取该会话的写串行锁（get-or-create）。

        调用方必须 `async with service.write_lock(sid):` 持有后再做追加/撤销写。
        """
        lock = self._write_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._write_locks[session_id] = lock
        return lock

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
        kind: str = "multi-agent",
        title: Optional[str] = None,
        session_id: Optional[str] = None,
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
            session_id=session_id,
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
        kind: Optional[str] = None,
        limit: int = 100,
    ) -> list[SessionInfo]:
        return repository.list_sessions(
            user_id, project_id=project_id, workspace_id=workspace_id,
            roots_only=roots_only, search=search, archived=archived, kind=kind, limit=limit,
        )

    def update(self, user_id: str, session_id: str, **fields: Any) -> SessionInfo:
        self._authorized(user_id, session_id)
        return repository.update_session(session_id, **fields)

    def remove(self, user_id: str, session_id: str) -> None:
        self._authorized(user_id, session_id)
        # 取消该会话及其子会话对应的 AgentBus 任务（对齐 session.ts:remove）
        child_ids = [c.id for c in repository.list_children(session_id)]
        task_bridge.cancel_children(child_ids)
        repository.remove_session(session_id)

    def children(self, user_id: str, parent_id: str) -> list[SessionInfo]:
        self._authorized(user_id, parent_id)
        return repository.list_children(parent_id)

    async def fork(self, user_id: str, session_id: str, message_id: Optional[str] = None) -> SessionInfo:
        """在指定消息（默认末尾）处克隆子会话，重建 parentID 映射（对齐 session.ts:fork）。

        拷贝截至 message_id（含）的消息到子会话；子会话获得独立消息日志与上下文，
        与父会话互不污染。消息对应的 message_parts 一并复制。

        边界修复：message_id 指向最后一条消息时正常复制（不再误报 MessageNotFound）；
        message_id 不存在时在创建子会话前即报错（不再留下满载消息的孤儿子会话）。
        """
        original = self._authorized(user_id, session_id)
        async with self.write_lock(session_id):
            source = repository.list_messages(session_id)
            if message_id:
                idx = next((i for i, m in enumerate(source) if m.id == message_id), None)
                if idx is None:
                    raise repository.MessageNotFound(message_id)
                cut = idx + 1
            else:
                cut = len(source)
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
            for m in source[:cut]:
                self._copy_message(session_id, child.id, m)
        return child

    @staticmethod
    def _copy_message(source_session: str, target_session: str, m: Message) -> None:
        """重放一条消息（含其 parts）到目标会话，保持类型/数据。"""
        new_msg = repository.append_message(target_session, m.type, m.data)
        for part in repository.list_parts(m.id):
            repository.append_part(target_session, new_msg.id, part.type, part.data)

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

    async def compact(self, user_id: str, session_id: str, checkpoint: str = "") -> None:
        """写压缩水位：落一条 compaction 消息并重建 epoch baseline。

        checkpoint 为空时用占位文案；落库后把 session.time_compacted 置为当前时间，
        使压缩基线持久化（重启/恢复后 history.load 仍能定位水位）。
        """
        self._authorized(user_id, session_id)
        async with self.write_lock(session_id):
            if not checkpoint:
                checkpoint = "[compacted]"
            # 定位压缩时模型视角的第一条原文作为 tail 起点 → 压缩后视角无损：
            # [checkpoint] + [tail 原文] + [压缩后新增]（与脚本/executor 路径一致）
            tail_snapshot: dict = {}
            load = session_history.load(session_id)
            for m in load.messages:
                if m.type != "compaction":
                    tail_snapshot = {"tail_start_id": m.id, "tail_start_seq": m.seq}
                    break
            repository.append_message(session_id, "compaction", {"content": checkpoint, "mode": "manual"})
            session_history.replace_epoch_after_compaction(session_id, checkpoint, tail_snapshot)
            repository.update_session(session_id, time_compacted=int(tmod.time() * 1000))

    async def revert(self, user_id: str, session_id: str, message_id: str) -> dict[str, Any]:
        """撤销到指定消息：删除其后的所有消息与部件（对齐 revert.ts）。

        级联：撤销点之后的 kind='task' 子会话（及其挂起的输入）一并移除，
        并取消对应 AgentBus 任务，防止"已撤销内容复活"。

        Returns:
            {"deleted": n, "messages": [...]}  剩余消息列表。
        """
        self._authorized(user_id, session_id)
        async with self.write_lock(session_id):
            deleted = repository.revert_to_message(session_id, message_id)
            # 级联撤销任务子会话 + 清除父会话待执行输入
            child_ids = [c.id for c in repository.list_children(session_id) if c.kind == "task"]
            task_bridge.cancel_children(child_ids)
            for cid in child_ids:
                repository.remove_session(cid)
            repository.clear_inputs(session_id)
            remaining = repository.list_messages(session_id)
        return {
            "deleted": deleted,
            "messages": [m.model_dump() for m in remaining],
        }

    def delete_message(self, user_id: str, session_id: str, message_id: str) -> bool:
        """删除单条消息及其 parts（对齐旧 chat.py 的 messages/{message_id}）。

        不级联子会话/不清输入（单条删除，保留会话其余历史）。
        """
        self._authorized(user_id, session_id)
        return repository.delete_message(session_id, message_id)

    # ── 输入 / 执行 ──────────────────────────────────────────────────────

    async def interrupt(self, user_id: str, session_id: str) -> None:
        """打断会话（级联：连同子会话一起打断，对齐 session.ts 级联取消）。"""
        self._authorized(user_id, session_id)
        child_ids = [c.id for c in repository.list_children(session_id)]
        # 级联取消子会话对应的 AgentBus 任务
        task_bridge.cancel_children(child_ids)
        # 丢弃排队中的输入：用户已取消，不希望在后续唤醒时再执行旧请求
        repository.clear_inputs(session_id)
        for cid in child_ids:
            repository.clear_inputs(cid)
        repository.update_session(session_id, status="interrupted")

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
