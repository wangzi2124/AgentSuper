"""平台 FastAPI 层依赖注入（对齐 agent.md §9 app/deps.py）。

`PlatformContext` 是单例运行时上下文：编译好的主图、工具注册表、虚拟文件系统、
会话存储、审批队列与指标记录器。通过 FastAPI 的 `Request.app.state.platform`
暴露，供各 router 依赖注入。

与既有 `backend/app`（RAG 应用）完全隔离：本包只服务于 agent.md 描述的
graph 平台，可独立启动：`uvicorn app.main:app`。
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from fastapi import Request

from execution.approval import ApprovalQueue
from execution.filesystem import (
    VirtualFileSystem,
    add_allowed_root,
    reset_allowed_roots,
    set_allowed_roots,
)
from execution.tool_registry import ToolRegistry
from graph.main_graph import build_main_graph, default_registry
from graph.subagent import SUBAGENTS
from observability.metrics import MetricsRecorder, get_recorder


def jsonable(obj: Any) -> Any:
    """把图状态/消息等转成可 JSON 序列化的结构（丢弃不可序列化值）。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    # LangChain BaseMessage
    role = getattr(obj, "type", None)
    if role is not None and hasattr(obj, "content"):
        return {"role": role, "content": jsonable(getattr(obj, "content", ""))}
    return str(obj)


def initial_state(question: str, user_id: str = "anonymous") -> dict[str, Any]:
    """构造一轮的输入状态。

    同一 thread 的 checkpoint 会保留上一轮的派生态（final_output / task_plan /
    sub_results 等），导致后续轮次被 supervisor 立即判定为「已完成」而短路。
    故每轮显式重置这些「本轮输出」字段（token_usage 走增量 reducer，不在此重置）。
    """
    from langchain_core.messages import HumanMessage

    return {
        "messages": [HumanMessage(content=question)],
        "iteration_count": 0,
        "user_id": user_id,
        "task_plan": [],
        "current_task_id": "",
        "sub_results": {},
        "draft_output": "",
        "final_output": "",
    }


@dataclass
class Session:
    """平台会话（thread）元数据。消息实体存在图 checkpointer 中。"""

    id: str
    thread_id: str
    title: str = ""
    directory: str | None = None
    user_id: str = "anonymous"
    kind: str = "multi-agent"
    status: str = "idle"
    model: str | None = None
    cost: float = 0.0
    tokens: dict[str, int] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    prompts: list[dict[str, Any]] = field(default_factory=list)
    parent_id: str | None = None
    # 逻辑删除的消息 id（delete_message / revert 用；底层 checkpoint 不动，仅过滤）
    hidden_ids: set[str] = field(default_factory=set)
    # 压缩基线：seq <= 基线的消息不再回放（替换为一条 system 摘要占位）
    compact_baseline_seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "title": self.title,
            "directory": self.directory,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_session_info(self) -> dict[str, Any]:
        """前端 `frontend/src/api/sessions.ts:SessionInfo` 形状（毫秒时间戳）。"""
        tok = self.tokens or {}
        return {
            "id": self.id,
            "slug": self.id,
            "user_id": self.user_id,
            "project_id": "",
            "workspace_id": None,
            "parent_id": self.parent_id,
            "directory": self.directory or "",
            "path": self.directory or "",
            "title": self.title or "新会话",
            "agent": None,
            "model": {"id": self.model, "providerID": "", "variant": None} if self.model else None,
            "kind": self.kind,
            "status": self.status,
            "cost": self.cost,
            "tokens_input": int(tok.get("input", 0)),
            "tokens_output": int(tok.get("output", 0)),
            "tokens_cache_read": int(tok.get("cache_read", 0)),
            "tokens_cache_write": int(tok.get("cache_write", 0)),
            "tokens_reasoning": int(tok.get("reasoning", 0)),
            "time_created": int(self.created_at * 1000),
            "time_updated": int(self.updated_at * 1000),
            "time_compacted": None,
            "time_archived": None,
        }


class SessionStore:
    """进程内会话存储（生产可替换为 Postgres，见 deploy/migrations）。"""

    def __init__(self) -> None:
        self._items: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        title: str = "",
        directory: str | None = None,
        user_id: str = "anonymous",
        session_id: str | None = None,
        kind: str = "multi-agent",
        model: str | None = None,
        parent_id: str | None = None,
    ) -> Session:
        sid = session_id or f"ses_{uuid.uuid4().hex[:16]}"
        sess = Session(
            id=sid,
            thread_id=sid,
            title=title or "新会话",
            directory=directory,
            user_id=user_id,
            kind=kind,
            model=model,
            parent_id=parent_id,
        )
        with self._lock:
            self._items[sid] = sess
        return sess

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._items.get(session_id)

    def ensure(
        self,
        session_id: str | None,
        *,
        directory: str | None = None,
        user_id: str = "anonymous",
        model: str | None = None,
    ) -> Session:
        if session_id and (sess := self.get(session_id)) is not None:
            # 会话中途改目录 / 模型：采纳本次携带的非空值
            if directory and directory != sess.directory:
                sess.directory = directory
            if model and model != sess.model:
                sess.model = model
            return sess
        return self.create(session_id=session_id, directory=directory, user_id=user_id, model=model)

    def list(
        self, *, user_id: str | None = None, kind: str | None = None
    ) -> list[Session]:
        with self._lock:
            items = list(self._items.values())
        if user_id is not None:
            items = [s for s in items if s.user_id == user_id]
        if kind is not None:
            items = [s for s in items if s.kind == kind]
        return sorted(items, key=lambda s: s.updated_at, reverse=True)

    def update(self, session_id: str, **fields: Any) -> Session | None:
        with self._lock:
            sess = self._items.get(session_id)
            if sess is None:
                return None
            for key, value in fields.items():
                if hasattr(sess, key) and value is not None:
                    setattr(sess, key, value)
            sess.updated_at = time.time()
            return sess

    def touch(self, session_id: str) -> None:
        with self._lock:
            if (sess := self._items.get(session_id)) is not None:
                sess.updated_at = time.time()

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._items.pop(session_id, None) is not None


@dataclass
class PlatformContext:
    """平台运行时上下文。"""

    graph: Any
    registry: ToolRegistry
    vfs: VirtualFileSystem
    sessions: SessionStore
    metrics: MetricsRecorder
    approvals: ApprovalQueue
    subagents: dict[str, Any] = field(default_factory=lambda: dict(SUBAGENTS))
    # 轮次快照：assistant 消息 id → restore descriptor（供 restore-snapshot / diff）
    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 在途回合取消标志（interrupt 置位；compat 流循环每步检查）
    cancels: dict[str, asyncio.Event] = field(default_factory=dict)
    # 并发闸门（对齐 MAX_CONCURRENT_AGENTS）：超出并发时 SSE 发 `queued`
    max_concurrent: int = 4
    max_queue_size: int = 20
    semaphore: asyncio.Semaphore | None = None
    queue_counter: int = 0
    # 审批桥接：非 None 时替代 PermissionManager 决策（测试/自定义）
    permission_hook: Callable[..., Any] | None = None

    def __post_init__(self) -> None:
        if self.semaphore is None:
            self.semaphore = asyncio.Semaphore(max(1, int(self.max_concurrent)))

    # ── 在途回合取消 ──────────────────────────────────────────────────────
    def begin_turn(self, session_id: str) -> asyncio.Event:
        """登记一个在途回合的取消标志（interrupt 借此中断）。"""
        event = asyncio.Event()
        self.cancels[session_id] = event
        return event

    def end_turn(self, session_id: str) -> None:
        self.cancels.pop(session_id, None)

    def cancel_turn(self, session_id: str) -> bool:
        """请求取消在途回合；返回是否确有在途回合。"""
        event = self.cancels.get(session_id)
        if event is None:
            return False
        event.set()
        return True

    async def run(self, question: str, *, session_id: str, user_id: str = "anonymous") -> dict[str, Any]:
        """执行一轮问答，返回最终状态（不含 BaseMessage 原始对象）。"""
        token = set_allowed_roots(self.allowed_roots_for(session_id))
        try:
            result = await self.graph.ainvoke(
                initial_state(question, user_id),
                config={"configurable": {"thread_id": session_id}},
            )
        finally:
            reset_allowed_roots(token)
        self.sessions.touch(session_id)
        return jsonable(result)

    async def stream(
        self, question: str, *, session_id: str, user_id: str = "anonymous"
    ) -> AsyncIterator[dict[str, Any]]:
        """逐节点流式执行，产出 `{event, node, data}` 事件。"""
        state = initial_state(question, user_id)
        yield {"event": "start", "data": {"session_id": session_id}}
        token = set_allowed_roots(self.allowed_roots_for(session_id))
        try:
            async for chunk in self.graph.astream(
                state,
                config={"configurable": {"thread_id": session_id}},
                stream_mode="updates",
            ):
                for node, update in (chunk or {}).items():
                    yield {"event": "node", "node": node, "data": jsonable(update)}
        finally:
            reset_allowed_roots(token)
        self.sessions.touch(session_id)
        snapshot = await self.state(session_id)
        yield {
            "event": "done",
            "data": {"final_output": snapshot.get("final_output", "")},
        }

    def allowed_roots_for(self, session_id: str) -> list[str]:
        """本会话允许写入/读取的额外根：会话目录 + 用户「工作目录」面板（PermissionManager）。

        沙箱默认根（`VirtualFileSystem.root`）不在此列（始终允许）。前端「工作目录」
        面板经 `/api/permission/workspaces` 落到 `PermissionManager.extra_workspaces`，
        此处把它接入平台 VFS —— 使面板配置对平台聊天真正生效（此前仅旧引擎读取）。
        无 `app` 子系统时（独立平台/tests）退化为仅会话目录。
        """
        roots: list[str] = []
        sess = self.sessions.get(session_id)
        if sess and sess.directory:
            roots.append(sess.directory)
        try:  # pragma: no cover - 依赖既有 app 子系统
            from execution.permission import get_manager

            roots.extend(get_manager().list_workspaces())
        except Exception:  # noqa: BLE001
            pass
        return roots

    async def state(self, session_id: str) -> dict[str, Any]:
        snap = await self.graph.aget_state({"configurable": {"thread_id": session_id}})
        return jsonable(getattr(snap, "values", {}) or {})

    def messages(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        msgs = state.get("messages") if isinstance(state, dict) else None
        return msgs if isinstance(msgs, list) else []

    @staticmethod
    def message_id(session_id: str, index: int) -> str:
        """按位置生成稳定消息 id（1-based，与历史回放一致）。"""
        return f"{session_id}_m{index}"

    @staticmethod
    def message_index(message_id: str) -> int | None:
        """从消息 id 反解位置（`<sid>_m<idx>` → idx）；不可解析返回 None。"""
        try:
            return int(message_id.rsplit("_m", 1)[1])
        except (IndexError, ValueError):
            return None

    async def session_messages(self, session_id: str) -> list[dict[str, Any]]:
        """把图状态消息映射成前端 `frontend/src/api/sessions.ts:SessionMessage` 形状。

        过滤逻辑删除（`hidden_ids`）与压缩基线（`compact_baseline_seq`）之后的消息；
        压缩基线 > 0 时前置一条 system 摘要占位（对齐 opencode 压缩检查点）。
        """
        _role = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
        sess = self.sessions.get(session_id)
        hidden = sess.hidden_ids if sess else set()
        baseline = sess.compact_baseline_seq if sess else 0
        state = await self.state(session_id)
        out: list[dict[str, Any]] = []
        for idx, msg in enumerate(self.messages(state), start=1):
            mid = self.message_id(session_id, idx)
            if mid in hidden or idx <= baseline:
                continue
            mtype = _role.get(str(msg.get("role", "system")), "system")
            content = msg.get("content", "")
            if not isinstance(content, str):
                content = jsonable(content)
                content = content if isinstance(content, str) else str(content)
            out.append(
                {
                    "id": mid,
                    "session_id": session_id,
                    "type": mtype,
                    "role": mtype,
                    "content": content,
                    "data": {"content": content},
                    "seq": idx,
                    "time_created": 0,
                    "parts": [],
                }
            )
        if baseline > 0:
            summary = f"（早前 {baseline} 条消息已压缩为上下文摘要）"
            out.insert(
                0,
                {
                    "id": f"{session_id}_compact",
                    "session_id": session_id,
                    "type": "system",
                    "role": "system",
                    "content": summary,
                    "data": {"content": summary, "compaction": True},
                    "seq": 0,
                    "time_created": 0,
                    "parts": [],
                },
            )
        return out

    async def _full_history(self, session_id: str) -> list[dict[str, Any]]:
        """未过滤的完整历史（含 hidden/基线之内），供 revert 计数与 fork 复制定位。"""
        _role = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
        state = await self.state(session_id)
        out: list[dict[str, Any]] = []
        for idx, msg in enumerate(self.messages(state), start=1):
            mtype = _role.get(str(msg.get("role", "system")), "system")
            content = msg.get("content", "")
            if not isinstance(content, str):
                content = jsonable(content)
                content = content if isinstance(content, str) else str(content)
            out.append({"id": self.message_id(session_id, idx), "seq": idx, "type": mtype, "content": content})
        return out

    async def hide_message(self, session_id: str, message_id: str) -> bool:
        """逻辑删除单条消息；返回是否命中。"""
        sess = self.sessions.get(session_id)
        if sess is None:
            return False
        if self.message_index(message_id) is None and not message_id.endswith("_compact"):
            return False
        sess.hidden_ids.add(message_id)
        sess.updated_at = time.time()
        return True

    async def revert_session(self, session_id: str, message_id: str) -> dict[str, Any]:
        """撤回 `message_id` 及其之后的所有消息（逻辑隐藏，保留 checkpoint）。

        返回 `{deleted, messages}`（messages = 撤回后剩余可见消息）。
        """
        sess = self.sessions.get(session_id)
        if sess is None:
            return {"deleted": 0, "messages": []}
        target = self.message_index(message_id)
        history = await self._full_history(session_id)
        if target is None:
            # 兼容非位置 id：按内容匹配找不到则视为无操作
            target = len(history) + 1
        deleted = 0
        for entry in history:
            if entry["seq"] >= target and entry["id"] not in sess.hidden_ids:
                sess.hidden_ids.add(entry["id"])
                deleted += 1
        # 压缩基线之后被撤回 → 基线回退到撤回点之前
        if target - 1 < sess.compact_baseline_seq:
            sess.compact_baseline_seq = max(0, target - 1)
        sess.updated_at = time.time()
        return {"deleted": deleted, "messages": await self.session_messages(session_id)}

    async def compact_session(self, session_id: str, keep_last: int = 10) -> dict[str, Any]:
        """压缩历史：保留最近 `keep_last` 条可见消息，之前折叠为摘要占位。"""
        sess = self.sessions.get(session_id)
        if sess is None:
            return {"compacted": False, "baseline": 0}
        history = await self._full_history(session_id)
        visible = [e for e in history if e["id"] not in sess.hidden_ids and e["seq"] > sess.compact_baseline_seq]
        if len(visible) <= keep_last:
            return {"compacted": False, "baseline": sess.compact_baseline_seq}
        baseline = visible[len(visible) - keep_last - 1]["seq"]
        sess.compact_baseline_seq = max(sess.compact_baseline_seq, baseline)
        sess.updated_at = time.time()
        return {"compacted": True, "baseline": sess.compact_baseline_seq}

    async def copy_messages_to_thread(
        self, src_id: str, dst_id: str, up_to_index: int | None = None
    ) -> int:
        """把 src 的可见消息复制到 dst 的 checkpoint（fork 用）。返回复制条数。"""
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        msgs = await self.session_messages(src_id)
        if up_to_index is not None:
            kept: list[dict[str, Any]] = []
            for m in msgs:
                kept.append(m)
                if m["id"] == self.message_id(src_id, up_to_index):
                    break
            msgs = kept
        lc_msgs: list[Any] = []
        for m in msgs:
            content = m.get("content", "")
            if m["type"] == "user":
                lc_msgs.append(HumanMessage(content=content))
            elif m["type"] == "assistant":
                lc_msgs.append(AIMessage(content=content))
            elif m["type"] == "system":
                lc_msgs.append(SystemMessage(content=content))
            # tool 消息依赖 tool_call_id，fork 时跳过以免产生非法状态
        if lc_msgs:
            await self.graph.aupdate_state(
                {"configurable": {"thread_id": dst_id}}, {"messages": lc_msgs}
            )
        return len(lc_msgs)

    async def fork_session(self, session_id: str, message_id: str | None = None) -> Session:
        """从 `message_id`（含）之前分叉出新会话，并把已可见消息复制进新线程。"""
        src = self.sessions.get(session_id)
        assert src is not None
        forked = self.sessions.create(
            title=f"{src.title} (分支)",
            directory=src.directory,
            user_id=src.user_id,
            kind=src.kind,
            model=src.model,
            parent_id=session_id,
        )
        index = self.message_index(message_id) if message_id else None
        await self.copy_messages_to_thread(session_id, forked.id, up_to_index=index)
        return forked

    def session_children(self, session_id: str) -> list[Session]:
        """按 parent_id 返回直接子会话（fork 分支）。"""
        return [s for s in self.sessions.list() if s.parent_id == session_id]

    async def request_permission(
        self,
        *,
        session_id: str,
        tool: str,
        args: dict[str, Any],
        emit: Callable[[dict[str, Any]], Any],
    ) -> bool:
        """发起一次工具审批（§9 `execution/approval.py` 高危审批流）。

        - 规范机制：`ctx.approvals`（`ApprovalQueue`）登记审批请求，是本平台权威记录。
        - 前端传输：复用既有 `/api/permission/*`（`app.permission.PermissionManager`）
          作为 UI 通道——它提供 `pending` 轮询与 `respond`，与前端 `stores/permission.ts`
          契约一致；`emit` 的 `permission_request` 事件用其 request_id，用户在该面板
          决策后再回填 `ctx.approvals`。无法导入该子系统时退化为纯平台审批。
        - `permission_hook` 非空时（测试/自定义）直接采用其决策。
        返回是否获批（allowed）。
        """
        if self.permission_hook is not None:
            return bool(
                await self.permission_hook(session_id=session_id, tool=tool, args=args, emit=emit)
            )

        # 沙箱/允许根内路径免审批（对齐 opencode external_directory ask：仅越界才询问用户）
        raw_path = args.get("path") or args.get("file") or args.get("workdir")
        if raw_path and self.vfs.is_allowed(str(raw_path)):
            return True

        rid = await self.approvals.request(tool, args, requester=session_id)
        path = str(raw_path) if raw_path else tool

        legacy = None
        try:  # pragma: no cover - 依赖既有 app 子系统
            from execution.permission import get_manager

            legacy = get_manager()
        except Exception:  # noqa: BLE001
            legacy = None

        if legacy is not None:
            req = legacy.create_request(
                path=path, operation="tool", tool_name=tool, tool_args=args, session_id=session_id
            )
            await emit(
                {
                    "type": "permission_request",
                    "request_id": req.id,
                    "path": req.path,
                    "operation": "tool",
                    "tool_name": tool,
                    "tool_args": args,
                    "created_at": req.created_at.isoformat(),
                    "expires_at": req.expires_at.isoformat(),
                }
            )
            decision = await legacy.await_decision(req.id)
            allowed = decision == "allowed"
            await self.approvals.decide(rid, allowed, reason=decision)
            if allowed and raw_path:
                add_allowed_root(str(raw_path))
            return allowed

        from execution.approval import APPROVAL_TIMEOUT_SEC

        await emit(
            {
                "type": "permission_request",
                "request_id": rid,
                "path": path,
                "operation": "tool",
                "tool_name": tool,
                "tool_args": args,
                "created_at": time.time(),
                "expires_at": time.time() + APPROVAL_TIMEOUT_SEC,
            }
        )
        allowed = await self.approvals.await_decision(rid) == "approved"
        if allowed and raw_path:
            add_allowed_root(str(raw_path))
        return allowed


def build_context(
    *,
    model: Any | None = None,
    registry: ToolRegistry | None = None,
    vfs: VirtualFileSystem | None = None,
    retrieve: Callable[..., list[dict[str, Any]]] | None = None,
    reranker: Any | None = None,
    sessions: SessionStore | None = None,
    metrics: MetricsRecorder | None = None,
) -> PlatformContext:
    """构建运行时上下文（默认装配 default_registry + 主图）。"""
    if registry is None:
        registry, vfs = default_registry(vfs)
    elif vfs is None:
        vfs = VirtualFileSystem()
    graph = build_main_graph(model=model, registry=registry, vfs=vfs, retrieve=retrieve, reranker=reranker)
    try:
        max_concurrent = int(os.environ.get("MAX_CONCURRENT_AGENTS", "4"))
    except ValueError:
        max_concurrent = 4
    try:
        max_queue_size = int(os.environ.get("MAX_QUEUE_SIZE", "20"))
    except ValueError:
        max_queue_size = 20
    return PlatformContext(
        graph=graph,
        registry=registry,
        vfs=vfs,
        sessions=sessions or SessionStore(),
        metrics=metrics or get_recorder(),
        approvals=ApprovalQueue(),
        max_concurrent=max_concurrent,
        max_queue_size=max_queue_size,
    )


def get_context(request: Request) -> PlatformContext:
    """FastAPI 依赖：取出挂在 app.state 上的平台上下文。"""
    ctx = getattr(request.app.state, "platform", None)
    if ctx is None:  # pragma: no cover - 启动配置错误
        raise RuntimeError("PlatformContext 未初始化（检查 lifespan）")
    return ctx
