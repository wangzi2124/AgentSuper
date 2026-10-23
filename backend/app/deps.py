"""平台 FastAPI 层依赖注入（对齐 agent.md §9 app/deps.py）。

`PlatformContext` 是单例运行时上下文：编译好的主图、工具注册表、虚拟文件系统、
会话存储、审批队列与指标记录器。通过 FastAPI 的 `Request.app.state.platform`
暴露，供各 router 依赖注入。

与既有 `backend/app`（RAG 应用）完全隔离：本包只服务于 agent.md 描述的
graph 平台，可独立启动：`uvicorn app.main:app`。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from fastapi import Request
from langgraph.types import Overwrite

from execution.approval import ApprovalQueue
from execution.filesystem import (
    VirtualFileSystem,
    add_allowed_root,
    reset_allowed_roots,
    set_allowed_roots,
)
from execution.tool_registry import ToolRegistry
from graph.main_graph import build_main_graph, default_registry
from graph.nodes.base import content_text
from graph.subagent import SUBAGENTS
from models.router import reset_request_model, set_request_model
from observability.metrics import MetricsRecorder, get_recorder

logger = logging.getLogger(__name__)


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


def _msg_role(msg: Any) -> str:
    """消息角色：原始 BaseMessage 用 `type`，已序列化 dict 用 `role`/`type`。"""
    if isinstance(msg, dict):
        return str(msg.get("role") or msg.get("type") or "system")
    return str(getattr(msg, "type", "system"))


def _msg_content(msg: Any) -> Any:
    if isinstance(msg, dict):
        return msg.get("content", "")
    return getattr(msg, "content", "")


def _msg_files(msg: Any) -> list[dict[str, Any]]:
    """读消息 `additional_kwargs["files"]`（聊天附件回显数据）。"""
    ak = (
        msg.get("additional_kwargs")
        if isinstance(msg, dict)
        else getattr(msg, "additional_kwargs", None)
    )
    files = ak.get("files") if isinstance(ak, dict) else None
    if not isinstance(files, list):
        return []
    return [f for f in files if isinstance(f, dict)]


def _msg_ui(msg: Any) -> dict[str, Any]:
    """尝试从 checkpoint 消息提取 `additional_kwargs["ui"]`（前端细节：agents/files_changed）。"""
    ak = (
        msg.get("additional_kwargs")
        if isinstance(msg, dict)
        else getattr(msg, "additional_kwargs", None)
    )
    if isinstance(ak, dict):
        ui = ak.get("ui")
        if isinstance(ui, dict):
            return ui
    return {}


def _vision_capable(model: str | None) -> bool:
    """模型是否声明「图像理解」能力（`模型管理` 的 capabilities.vision）。"""
    if not model:
        return False
    try:
        from models.catalog import get_catalog, read_capabilities

        caps = read_capabilities(model)
        if not caps:  # 未按全名收录 → 按末段名匹配（无 provider 前缀时）
            mid = str(model).rsplit("/", 1)[-1]
            for entry in get_catalog():
                if str(entry.get("id", "")).rsplit("/", 1)[-1] == mid:
                    caps = entry.get("capabilities") or {}
                    break
        return bool(caps.get("vision"))
    except Exception:  # noqa: BLE001 - 目录不可用时按「无视觉」降级
        return False


def _image_blocks(text: str, images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把纯文本 + 图片附件拼成多模态 content 分块。"""
    blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for f in images:
        data = str(f.get("data") or "")
        if not data:
            continue
        mime = str(f.get("mime_type") or "image/png")
        url = data if data.startswith("data:") else f"data:{mime};base64,{data}"
        blocks.append({"type": "image_url", "image_url": {"url": url}})
    return blocks


def _attachment_content(
    question: str, files: list[Any] | None, model: str | None
) -> Any:
    """把聊天附件并入用户消息 content：图片（模型声明 vision 时）→ `image_url` 分块。

    非图片附件 / 模型无视觉能力 → 只在文本里列出文件名（不臆造解析结果）。
    返回 `str`（纯文本）或分块 `list[dict]`（多模态）——两者 litellm 均接受。
    """
    items = [f for f in (files or []) if isinstance(f, dict) and f.get("data")]
    if not items:
        return question
    names = [str(f.get("filename") or "附件") for f in items]
    text = f"{question}\n\n[附件：{'、'.join(names)}]"
    images = [f for f in items if str(f.get("mime_type") or "").startswith("image/")]
    if not images:
        return text
    if not _vision_capable(model):
        return text + "\n（当前模型未声明图像理解能力，图片未随请求发送）"
    text += (
        "\n（图片已随本条消息内联提供，请直接依据图像内容作答；"
        "不要在工作区中搜索该文件——它不在磁盘上。）"
    )
    return _image_blocks(text, images)


_FILE_ECHO_KEYS = ("filename", "mime_type", "data", "_thumb", "_caption")


def _files_payload(files: list[Any] | None) -> list[dict[str, str]]:
    """挑出可回显的附件字段（仅字符串），存进消息 `additional_kwargs` 供历史回放。"""
    out: list[dict[str, str]] = []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        item = {
            k: str(v)
            for k, v in f.items()
            if k in _FILE_ECHO_KEYS and isinstance(v, str) and v
        }
        if item:
            out.append(item)
    return out


def initial_state(
    question: str,
    user_id: str = "anonymous",
    *,
    files: list[Any] | None = None,
    model: str | None = None,
    use_vector_db: bool = False,
    message_id: str | None = None,
    context_summary: str = "",
    context_tail_start_id: str | None = None,
    context_bound: bool = False,
    context_window_override: int = 0,
    compress_threshold_override: int = 0,
    keep_recent_tokens_override: int = 0,
) -> dict[str, Any]:
    """构造一轮的输入状态。

    同一 thread 的 checkpoint 会保留上一轮的派生态（final_output / task_plan /
    sub_results 等），导致后续轮次被 supervisor 立即判定为「已完成」而短路。
    故每轮显式重置这些「本轮输出」字段（token_usage 走增量 reducer，不在此重置）。

    `files`（前端聊天附件，`{filename, mime_type, data(base64)}`）与 `model`（本回合
    选中模型 id）一并传入 → 图片按模型 `vision` 能力组装成多模态 content 分块。
    附件本身另存进 `additional_kwargs["files"]`（含 `_thumb`/`_caption`），供历史回放
    在聊天里回显图片——正文 content 只承担喂给模型的部分。
    """
    from langchain_core.messages import HumanMessage

    # 稳定消息 ID：跨模型/协议切换后保持不变（禁止 index 重算）。
    msg = HumanMessage(
        content=_attachment_content(question, files, model),
        id=message_id or f"um_{uuid.uuid4().hex[:12]}",
    )
    payload = _files_payload(files)
    if payload:
        msg.additional_kwargs["files"] = payload

    # §2.4 C1：会话预算须真实下发，否则扇出门禁 `_remaining_budget` 恒为 0 →
    # 并行扇出在生产永久不可达。未配置（<=0）时不下发该字段，门禁按「未知预算」拒绝。
    from config import settings

    budget_total = float(getattr(settings, "session_budget", 0.0) or 0.0)

    # 压缩配置与「本轮模型」成对绑定：目标窗口更小 → 阈值同步调低，
    # 禁止复用旧模型/全局默认阈值（agent-3 §2.2）。窗口未知时保留默认值。
    context_window = 0
    compress_threshold = 0
    keep_recent_tokens = 0
    if model:
        try:
            from models.catalog import read_limits

            context_window = int(read_limits(model).get("context_length") or 0)
        except Exception:  # noqa: BLE001 - 目录不可用时按未知处理
            context_window = 0
    if context_window > 0:
        compress_threshold = max(2_000, int(context_window * 0.6))
        keep_recent_tokens = max(1_000, min(20_000, int(context_window * 0.25)))
    # 切换绑定的压缩配置优先：即使目录临时不可用，也不能退回旧模型/全局阈值。
    if context_window_override > 0:
        context_window = context_window_override
        if compress_threshold_override <= 0:
            compress_threshold = max(2_000, int(context_window * 0.6))
        if keep_recent_tokens_override <= 0:
            keep_recent_tokens = max(1_000, min(20_000, int(context_window * 0.25)))
    if compress_threshold_override > 0:
        compress_threshold = compress_threshold_override
    if keep_recent_tokens_override > 0:
        keep_recent_tokens = keep_recent_tokens_override

    state: dict[str, Any] = {
        "messages": [msg],
        "iteration_count": 0,
        "user_id": user_id,
        "task_plan": [],
        "plan_tier": "",
        "current_task_id": "",
        # sub_results 是 `merge_dicts` reducer 通道：直接传 `{}` 只会与上一轮
        # checkpoint 里的结果合并（空字典是 no-op），导致新一轮 synthesize 取到
        # 旧答案。用 Overwrite 绕过 reducer 强制清空。
        "sub_results": Overwrite({}),
        "draft_output": "",
        "final_output": "",
        "context_compressed": False,
        "context_bound": bool(context_bound),
        "context_window": context_window,
        "compress_threshold": compress_threshold,
        "keep_recent_tokens": keep_recent_tokens,
        "rag_context": "",
        "rag_done": False,
        "use_vector_db": bool(use_vector_db),
        # §13.4.3 单轮字段：每轮重置，避免上一轮的「相关/已改写」污染本轮。
        "rag_relevant": False,
        "rewrite_count": 0,
        "rewritten_query": "",
    }
    # 模型切换绑定生效时，显式写入（含空值/None）以清掉上一次绑定的残留；
    # 否则不覆盖 checkpoint 里已有的 context_summary（自动压缩摘要必须跨轮保留）。
    if context_bound:
        state["context_summary"] = context_summary
        state["context_tail_start_id"] = context_tail_start_id
    else:
        if context_summary:
            state["context_summary"] = context_summary
        if context_tail_start_id:
            state["context_tail_start_id"] = context_tail_start_id
    if budget_total > 0:
        state["budget_total"] = budget_total
    return state


class ModelSwitchError(Exception):
    """模型切换被门禁拒绝（携带 HTTP 状态码，供 API 层直接映射）。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


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
    """会话存储：进程内热缓存 + session.db 落库（跨重启水合）。

    - 内存 dict 是运行期热路径（列表/取用/在途轮次状态）。
    - create/update/touch/delete 同步写 `session.db`（repository.write-through）——
      后端重启后 `get()` 懒水合、`list()` 按用户水合，会话列表与元数据不再丢失。
    - 消息实体的唯一真相源是**图 checkpointer**（AsyncSqliteSaver，
      `graph/checkpoint.py`），不写 session_messages 表。
    - 落库失败仅记日志不阻塞聊天（库不可用时行为退回纯内存）。
    """

    def __init__(self) -> None:
        self._items: dict[str, Session] = {}
        self._lock = threading.Lock()

    # ── 落库（write-through） ──────────────────────────────────────────────

    @staticmethod
    def _session_from_info(info: Any) -> Session:
        """repository.SessionInfo → 内存 Session（重启水合用）。"""
        model = getattr(info, "model", None)
        if hasattr(model, "id"):
            model = model.id
        elif isinstance(model, dict):
            model = model.get("id")
        created = float(getattr(info, "time_created", 0) or 0) / 1000
        updated = float(getattr(info, "time_updated", 0) or 0) / 1000
        now = time.time()
        return Session(
            id=info.id,
            thread_id=info.id,
            title=info.title or "新会话",
            directory=info.directory or None,
            user_id=info.user_id or "anonymous",
            kind=info.kind or "multi-agent",
            status=info.status or "idle",
            model=model if isinstance(model, str) and model else None,
            cost=float(info.cost or 0.0),
            tokens={
                "input": int(info.tokens_input or 0),
                "output": int(info.tokens_output or 0),
                "cache_read": int(info.tokens_cache_read or 0),
                "cache_write": int(info.tokens_cache_write or 0),
                "reasoning": int(getattr(info, "tokens_reasoning", 0) or 0),
            },
            created_at=created if created > 0 else now,
            updated_at=updated if updated > 0 else now,
            parent_id=getattr(info, "parent_id", None),
            hidden_ids=set(getattr(info, "hidden_ids", None) or []),
            compact_baseline_seq=int(getattr(info, "compact_baseline_seq", 0) or 0),
        )

    def _persist_create(self, sess: Session) -> None:
        try:
            from .session import repository as repo

            project = repo.resolve_project(sess.directory or ".")
            repo.create_session(
                sess.user_id,
                project.id,
                sess.directory or "",
                path=sess.directory or "",
                parent_id=sess.parent_id,
                model=sess.model,
                kind=sess.kind,
                title=sess.title,
                session_id=sess.id,
            )
        except Exception:  # noqa: BLE001 - 同 id 行已存在等 → 退化为整份更新
            logger.debug("session 落库 create 失败，改走 update: %s", sess.id, exc_info=True)
            self.persist(sess.id)

    def persist(self, session_id: str) -> None:
        """把内存会话的可持久化字段整份写回 session.db（幂等；顺带刷新 time_updated）。"""
        sess = self.get(session_id)
        if sess is None:
            return
        try:
            from .session import repository as repo

            repo.update_session(
                session_id,
                title=sess.title,
                status=sess.status,
                model=sess.model,
                directory=sess.directory or "",
                cost=round(float(sess.cost or 0.0), 8),
                tokens_input=int((sess.tokens or {}).get("input", 0)),
                tokens_output=int((sess.tokens or {}).get("output", 0)),
                tokens_cache_read=int((sess.tokens or {}).get("cache_read", 0)),
                tokens_cache_write=int((sess.tokens or {}).get("cache_write", 0)),
                hidden_ids=sess.hidden_ids,
                compact_baseline_seq=int(sess.compact_baseline_seq or 0),
            )
        except Exception:  # noqa: BLE001 - 落库失败不阻塞聊天
            logger.warning("session 落库 update 失败: %s", session_id, exc_info=True)

    # ── 水合（重启后从 session.db 恢复） ────────────────────────────────────

    def hydrate_rows(self, infos: list[Any]) -> None:
        """把库里已有、内存缺失的会话行补进内存（幂等）。"""
        missing = []
        for info in infos:
            with self._lock:
                if info.id not in self._items:
                    missing.append(info)
        if not missing:
            return
        with self._lock:
            for info in missing:
                self._items.setdefault(info.id, self._session_from_info(info))

    def _hydrate_one(self, session_id: str) -> Session | None:
        try:
            from .session import repository as repo

            info = repo.get_session(session_id)
        except Exception:  # noqa: BLE001
            return None
        return self._session_from_info(info) if info else None

    # ── CRUD ──────────────────────────────────────────────────────────────

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
        self._persist_create(sess)
        return sess

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            sess = self._items.get(session_id)
        if sess is not None:
            return sess
        # 重启水合：内存未命中 → 从 session.db 懒加载（并发下 setdefault 保唯一）
        sess = self._hydrate_one(session_id)
        if sess is None:
            return None
        with self._lock:
            return self._items.setdefault(session_id, sess)

    def ensure(
        self,
        session_id: str | None,
        *,
        directory: str | None = None,
        user_id: str = "anonymous",
        model: str | None = None,
    ) -> Session:
        if session_id and (sess := self.get(session_id)) is not None:
            # 会话中途改目录：采纳本次携带的非空值。
            # 模型不再由 ensure 静默更新 —— 切换必须走 `switch_model` 门禁/迁移。
            changed = False
            if directory and directory != sess.directory:
                sess.directory = directory
                changed = True
            if changed:
                sess.updated_at = time.time()
                self.persist(session_id)
            return sess
        return self.create(session_id=session_id, directory=directory, user_id=user_id, model=model)

    def list(
        self, *, user_id: str | None = None, kind: str | None = None
    ) -> list[Session]:
        if user_id is not None:
            # 重启水合：先从库补齐该用户的缺失会话，再走内存视图
            try:
                from .session import repository as repo

                infos = repo.list_sessions(user_id, kind=kind, archived=None, limit=1000)
                self.hydrate_rows(infos)
            except Exception:  # noqa: BLE001
                logger.debug("session 列表水合失败（库不可用时忽略）", exc_info=True)
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
        self.persist(session_id)
        return sess

    def touch(self, session_id: str) -> None:
        with self._lock:
            sess = self._items.get(session_id)
        if sess is not None:
            self.persist(session_id)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            existed = self._items.pop(session_id, None) is not None
        try:
            from .session import repository as repo

            repo.remove_session(session_id)  # 递归删子会话（行不存在时静默）
        except Exception:  # noqa: BLE001
            logger.warning("session 落库 delete 失败: %s", session_id, exc_info=True)
        return existed


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
    # 会话级已审批外部路径：回合开始时并入允许根（跨回合/轮次快照均生效）
    approved_roots: dict[str, list[str]] = field(default_factory=dict)
    # 在途回合取消标志（interrupt 置位；compat 流循环每步检查）
    cancels: dict[str, asyncio.Event] = field(default_factory=dict)
    # 并发闸门（对齐 MAX_CONCURRENT_AGENTS）：超出并发时 SSE 发 `queued`
    max_concurrent: int = 4
    max_queue_size: int = 20
    semaphore: asyncio.Semaphore | None = None
    queue_counter: int = 0
    # 审批桥接：非 None 时替代 PermissionManager 决策（测试/自定义）
    permission_hook: Callable[..., Any] | None = None
    # 技能加载器（斜杠命令 / `load_skill_*` 工具）；None = 未接线
    skill_loader: Any | None = None

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

    def session_model(self, session_id: str) -> str | None:
        """会话当前选中的模型 id（前端「模型管理」选择器写入；未选时为 None）。"""
        sess = self.sessions.get(session_id)
        return getattr(sess, "model", None) if sess is not None else None

    def context_binding(self, session_id: str) -> dict[str, Any]:
        """读取本会话的模型切换上下文绑定（摘要 + 近期原文起点）。

        绑定存放在 `session_context_epoch.snapshot`，不删改图 checkpoint 原文；
        模型视角 = `[摘要] + [tail_start_id 起的近期原文] + [切换后新消息]`。
        自动压缩写入的 snapshot 同样在此返回，二者兼容。
        """
        try:
            from .session import repository as repo

            epoch = repo.get_epoch(session_id)
        except Exception:  # noqa: BLE001
            logger.debug("读取上下文绑定失败", exc_info=True)
            return {}
        snap = dict(epoch.snapshot or {}) if epoch else {}
        summary = snap.get("summary")
        if not isinstance(summary, str):
            summary = ""
        tail = snap.get("tail_start_id")
        return {
            "active": bool(snap.get("model_switch")),
            "summary": summary,
            "tail_start_id": tail if isinstance(tail, str) else None,
            "context_window": int(snap.get("context_window") or 0),
            "compress_threshold": int(snap.get("compress_threshold") or 0),
            "keep_recent_tokens": int(snap.get("keep_recent_tokens") or 0),
        }

    async def resolve_context_binding(self, session_id: str) -> dict[str, Any]:
        """解析本轮生效的上下文绑定。

        模型切换后若图内自动压缩又生成了更新的摘要，以图内摘要为准（并沿用其
        tail 起点），否则旧的绑定摘要会在下一轮把新摘要覆盖回去。
        """
        binding = self.context_binding(session_id)
        if not binding.get("active"):
            return binding
        try:
            snap = await self.state(session_id)
        except Exception:  # noqa: BLE001 - 无 checkpoint / 读取失败按绑定原值
            return binding
        graph_summary = str(snap.get("context_summary") or "")
        if graph_summary and graph_summary != (binding.get("summary") or ""):
            return {
                "active": True,
                "summary": graph_summary,
                "tail_start_id": snap.get("context_tail_start_id"),
                "context_window": binding.get("context_window", 0),
                "compress_threshold": binding.get("compress_threshold", 0),
                "keep_recent_tokens": binding.get("keep_recent_tokens", 0),
            }
        return binding

    def _write_switch_backup(
        self,
        session_id: str,
        old_model: str | None,
        target: str,
        messages: list[Any],
        tokens: int,
        threshold: int,
        keep_tokens: int,
    ) -> str:
        """迁移前原文快照（必须先于任何摘要/绑定写入）。"""
        from langchain_core.messages import messages_to_dict

        backup_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data", "model_switches"
        )
        os.makedirs(backup_dir, exist_ok=True)
        stamp = int(time.time() * 1000)
        path = os.path.join(backup_dir, f"{session_id}_{stamp}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "session_id": session_id,
                    "from": old_model,
                    "to": target,
                    "tokens": tokens,
                    "threshold": threshold,
                    "keep_recent_tokens": keep_tokens,
                    "status": "prepared",
                    "messages": messages_to_dict(messages),
                },
                fh,
                ensure_ascii=False,
            )
        return path

    @staticmethod
    def _mark_switch_backup(path: str, status: str) -> None:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            data["status"] = status
            data["updated_at"] = time.time()
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
        except Exception:  # noqa: BLE001 - 标记失败不掩盖主流程结果
            logger.warning("更新模型切换备份标记失败: %s", path, exc_info=True)

    async def switch_model(self, session_id: str, target: str) -> dict[str, Any]:
        """切换会话模型：门禁 → 生成摘要 → 原子写入绑定 → 持久化模型 id。

        失败抛 `ModelSwitchError(status_code)`，旧模型与会话绑定保持不变。
        """
        target = (target or "").strip()
        if not target:
            raise ModelSwitchError("目标模型为空", 400)
        sess = self.sessions.get(session_id)
        if sess is None:
            raise ModelSwitchError("会话不存在", 404)
        if target == sess.model:
            return {
                "switched": False,
                "model": sess.model,
                "summarized": False,
            }
        from models.catalog import read_limits

        limits = read_limits(target) or {}
        window = int(limits.get("context_length") or 0)
        if window <= 0:
            raise ModelSwitchError(f"目标模型上下文窗口未知，已拒绝切换：{target}", 400)
        if sess.status == "running" or session_id in self.cancels:
            raise ModelSwitchError("会话正在运行，请等待本轮完成后再切换模型", 409)

        messages = await self._raw_messages(session_id)
        from cognition.compressor import (
            compress_context,
            enforce_precision,
            estimate_tokens,
            extract_precision_values,
            serialize_messages,
        )

        tokens = (
            sum(estimate_tokens(serialize_messages([m])) for m in messages)
            if messages
            else 0
        )
        threshold = max(2_000, int(window * 0.6))
        keep_tokens = max(1_000, min(20_000, int(window * 0.25)))
        summary = ""
        tail_start_id: str | None = None
        old_model = sess.model
        before_ids = [
            PlatformContext.stable_message_id(session_id, m) for m in messages
        ]

        # §2.3：原文快照必须先于摘要/绑定写入；备份失败即拒绝切换。
        try:
            backup_path = PlatformContext._write_switch_backup(
                self,
                session_id, old_model, target, messages, tokens, threshold, keep_tokens
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("切换模型备份失败: %s", exc)
            raise ModelSwitchError(f"迁移备份失败，已保留原模型：{exc}", 500) from exc

        recent: list[Any] = []
        try:
            if tokens > threshold and messages:
                existing_summary = self.context_binding(session_id).get("summary", "")
                try:
                    graph_summary = str(
                        (await self.state(session_id)).get("context_summary") or ""
                    )
                except Exception:  # noqa: BLE001 - 空会话 / 无 checkpoint
                    graph_summary = ""
                if graph_summary and graph_summary != existing_summary:
                    existing_summary = graph_summary
                model_token = set_request_model(target)
                try:
                    result = await compress_context(
                        messages,
                        existing_summary=existing_summary,
                        model=None,
                        threshold=threshold,
                        keep_tokens=keep_tokens,
                        raise_on_error=True,
                        segment_tokens=max(4_000, min(12_000, threshold // 4)),
                    )
                finally:
                    reset_request_model(model_token)
                summary = str(result.get("summary") or "").strip()
                if not summary:
                    raise ModelSwitchError("历史无法压缩到目标模型窗口，已保留原模型", 400)
                recent = list(result.get("recent") or [])
                recent_ids = {id(m) for m in recent}
                older = [m for m in messages if id(m) not in recent_ids]
                summary = enforce_precision(summary, older)
                # §3.2 确定性事实探针：原文中的路径/命令/数字必须在摘要+近期原文中。
                preserved = f"{summary}\n{serialize_messages(recent)}"
                facts = extract_precision_values(serialize_messages(messages))
                missing = [t for t in facts if t not in preserved]
                if missing:
                    raise ModelSwitchError(
                        f"事实探针失败，缺少 {len(missing)} 个精确值，已保留原模型："
                        + ", ".join(missing[:5]),
                        500,
                    )
                if recent:
                    tail = recent[0]
                    tail_start_id = (
                        getattr(tail, "id", None)
                        or PlatformContext.stable_message_id(session_id, tail)
                    )
        except ModelSwitchError:
            PlatformContext._mark_switch_backup(backup_path, "rolled_back")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("切换模型时历史摘要失败：%s", exc)
            PlatformContext._mark_switch_backup(backup_path, "rolled_back")
            raise ModelSwitchError(f"历史摘要生成失败，已保留原模型：{exc}", 500) from exc

        from .session import repository as repo

        epoch = repo.get_epoch(session_id)
        baseline = epoch.baseline if epoch else ""
        # 无既有纪元时用 0（不是 latest_seq）：切换只新增摘要绑定，
        # 不能让 history.load 把切换前的全部消息当成「基线之前」跳过。
        baseline_seq = epoch.baseline_seq if epoch else 0
        old_snapshot = dict(epoch.snapshot or {}) if epoch else {}
        snapshot = dict(old_snapshot)
        snapshot["context_window"] = window
        snapshot["compress_threshold"] = threshold
        snapshot["keep_recent_tokens"] = keep_tokens
        if summary:
            snapshot["summary"] = summary
            snapshot["tail_start_id"] = tail_start_id
            snapshot["summary_model"] = target
            snapshot["summary_at"] = time.time()
        # 只有确实存在摘要/近期原文绑定时才标记生效；否则不干扰图内自动压缩摘要。
        if snapshot.get("summary") or snapshot.get("tail_start_id"):
            snapshot["model_switch"] = True
        try:
            repo.upsert_epoch(session_id, baseline, baseline_seq, snapshot)
        except Exception as exc:  # noqa: BLE001 - 绑定写失败必须保留旧模型
            logger.warning("模型切换绑定写入失败：%s", exc)
            PlatformContext._mark_switch_backup(backup_path, "rolled_back")
            raise ModelSwitchError(f"模型绑定写入失败，已保留原模型：{exc}", 500) from exc

        # §3.1 消息完整性：切换不得丢失任何未压缩消息。
        after_ids = [
            PlatformContext.stable_message_id(session_id, m)
            for m in await self._raw_messages(session_id)
        ]
        lost = [mid for mid in before_ids if mid not in after_ids]
        if lost:
            try:
                repo.upsert_epoch(session_id, baseline, baseline_seq, old_snapshot)
            except Exception:  # noqa: BLE001
                logger.warning("回滚上下文绑定失败", exc_info=True)
            PlatformContext._mark_switch_backup(backup_path, "rolled_back")
            raise ModelSwitchError(
                f"消息完整性校验失败，丢失 {len(lost)} 条消息，已回滚模型切换", 500
            )

        try:
            sess.model = target
            sess.updated_at = time.time()
            self.sessions.persist(session_id)
        except Exception as exc:  # noqa: BLE001 - 持久化失败必须回滚绑定
            sess.model = old_model
            try:
                repo.upsert_epoch(session_id, baseline, baseline_seq, old_snapshot)
            except Exception:  # noqa: BLE001
                logger.warning("回滚上下文绑定失败", exc_info=True)
            PlatformContext._mark_switch_backup(backup_path, "rolled_back")
            raise ModelSwitchError(f"模型持久化失败，已保留原模型：{exc}", 500) from exc

        PlatformContext._mark_switch_backup(backup_path, "committed")
        try:
            backup_dir = os.path.dirname(backup_path)
            with open(
                os.path.join(backup_dir, "switches.jsonl"), "a", encoding="utf-8"
            ) as fh:
                fh.write(
                    json.dumps(
                        {
                            "session_id": session_id,
                            "from": old_model,
                            "to": target,
                            "tokens": tokens,
                            "threshold": threshold,
                            "summarized": bool(summary),
                            "at": time.time(),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:  # noqa: BLE001 - 审计失败不影响已提交切换
            logger.warning("写入模型切换审计失败", exc_info=True)

        return {
            "switched": True,
            "from": old_model,
            "to": target,
            "summarized": bool(summary),
            "tail_start_id": tail_start_id,
            "context_window": window,
            "compress_threshold": threshold,
            "keep_recent_tokens": keep_tokens,
        }

    async def run(
        self,
        question: str,
        *,
        session_id: str,
        user_id: str = "anonymous",
        files: list[Any] | None = None,
        use_vector_db: bool = False,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        """执行一轮问答，返回最终状态（不含 BaseMessage 原始对象）。"""
        # `/技能名`：本轮强制加载技能正文（找不到/未启用则原样发送）
        from .skills.slash import apply_slash_skill

        question, _skill = apply_slash_skill(question, self.skill_loader)
        token = set_allowed_roots(self.allowed_roots_for(session_id))
        model = self.session_model(session_id)
        binding = await self.resolve_context_binding(session_id)
        model_token = set_request_model(model)
        sess = self.sessions.get(session_id)
        if sess is not None:
            sess.status = "running"
        self.begin_turn(session_id)
        try:
            result = await self.graph.ainvoke(
                initial_state(
                    question, user_id, files=files, model=model,
                    use_vector_db=use_vector_db,
                    message_id=message_id,
                    context_summary=binding.get("summary", ""),
                    context_tail_start_id=binding.get("tail_start_id"),
                    context_bound=binding.get("active", False),
                    context_window_override=binding.get("context_window", 0),
                    compress_threshold_override=binding.get("compress_threshold", 0),
                    keep_recent_tokens_override=binding.get("keep_recent_tokens", 0),
                ),
                config={"configurable": {"thread_id": session_id}},
            )
        finally:
            if sess is not None:
                sess.status = "idle"
            self.end_turn(session_id)
            reset_allowed_roots(token)
            reset_request_model(model_token)
        self.sessions.touch(session_id)
        return jsonable(result)

    async def stream(
        self,
        question: str,
        *,
        session_id: str,
        user_id: str = "anonymous",
        files: list[Any] | None = None,
        use_vector_db: bool = False,
    ) -> AsyncIterator[dict[str, Any]]:
        """逐节点流式执行，产出 `{event, node, data}` 事件。"""
        # `/技能名`：本轮强制加载技能正文（找不到/未启用则原样发送）
        from .skills.slash import apply_slash_skill

        question, _skill = apply_slash_skill(question, self.skill_loader)
        model = self.session_model(session_id)
        binding = await self.resolve_context_binding(session_id)
        state = initial_state(
            question, user_id, files=files, model=model, use_vector_db=use_vector_db,
            context_summary=binding.get("summary", ""),
            context_tail_start_id=binding.get("tail_start_id"),
            context_bound=binding.get("active", False),
            context_window_override=binding.get("context_window", 0),
            compress_threshold_override=binding.get("compress_threshold", 0),
            keep_recent_tokens_override=binding.get("keep_recent_tokens", 0),
        )
        yield {"event": "start", "data": {"session_id": session_id}}
        token = set_allowed_roots(self.allowed_roots_for(session_id))
        model_token = set_request_model(model)
        sess = self.sessions.get(session_id)
        if sess is not None:
            sess.status = "running"
        self.begin_turn(session_id)
        try:
            async for chunk in self.graph.astream(
                state,
                config={"configurable": {"thread_id": session_id}},
                stream_mode="updates",
            ):
                for node, update in (chunk or {}).items():
                    yield {"event": "node", "node": node, "data": jsonable(update)}
        finally:
            if sess is not None:
                sess.status = "idle"
            self.end_turn(session_id)
            reset_allowed_roots(token)
            reset_request_model(model_token)
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

            mgr = get_manager()
            roots.extend(mgr.list_workspaces())
            roots.extend(mgr.list_whitelist())
        except Exception:  # noqa: BLE001
            pass
        roots.extend(self.approved_roots.get(session_id, ()))
        return roots

    def remember_approved_root(self, session_id: str, path: str) -> None:
        """记住本会话已审批通过的外部路径（跨回合允许根，去重）。

        审批发生在图任务上下文里，`add_allowed_root` 只就地更新该上下文的 contextvar，
        不会传播回请求级（轮次快照 / 后续回合的读写）—— 故在 `PlatformContext` 上按会话
        留档，由 `allowed_roots_for`（回合开始时经 `set_allowed_roots` 注入）统一并入。
        """
        if not path:
            return
        try:
            from pathlib import Path

            norm = str(Path(path).expanduser().resolve())
        except (OSError, RuntimeError, ValueError):
            norm = path
        roots = self.approved_roots.setdefault(session_id, [])
        if norm not in roots:
            roots.append(norm)

    async def state(self, session_id: str) -> dict[str, Any]:
        snap = await self.graph.aget_state({"configurable": {"thread_id": session_id}})
        return jsonable(getattr(snap, "values", {}) or {})

    async def _raw_messages(self, session_id: str) -> list[Any]:
        """原始消息对象（不经 `jsonable`），用于读取 `additional_kwargs`（附件回显）。"""
        snap = await self.graph.aget_state({"configurable": {"thread_id": session_id}})
        values = getattr(snap, "values", {}) or {}
        msgs = values.get("messages") if isinstance(values, dict) else None
        return msgs if isinstance(msgs, list) else []

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

    @staticmethod
    def stable_message_id(session_id: str, msg: Any) -> str:
        """稳定消息 id：优先 checkpoint 消息 id（跨模型/协议切换不变）。

        无 id 时按「角色 + 内容」生成确定性摘要 id；同一消息重复计算结果一致。
        禁止用 index 兜底（位置会随压缩/撤回/分叉漂移）。
        """
        mid = getattr(msg, "id", None)
        if mid:
            return str(mid)
        content = _msg_content(msg)
        raw = (
            content
            if isinstance(content, str)
            else json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
        )
        digest = hashlib.sha256(
            f"{_msg_role(msg)}|{raw}".encode("utf-8")
        ).hexdigest()[:16]
        return f"msg_{digest}"

    async def session_messages(self, session_id: str) -> list[dict[str, Any]]:
        """把图状态消息映射成前端 `frontend/src/api/sessions.ts:SessionMessage` 形状。

        过滤逻辑删除（`hidden_ids`）与压缩基线（`compact_baseline_seq`）之后的消息；
        压缩基线 > 0 时前置一条 system 摘要占位（对齐 opencode 压缩检查点）。
        """
        _role = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
        sess = self.sessions.get(session_id)
        hidden = sess.hidden_ids if sess else set()
        baseline = sess.compact_baseline_seq if sess else 0
        raw = await self._raw_messages(session_id)
        out: list[dict[str, Any]] = []
        for idx, msg in enumerate(raw, start=1):
            mid = PlatformContext.stable_message_id(session_id, msg)
            # 兼容旧版 hidden_ids（`<sid>_m<idx>`）与基线水位（seq）。
            legacy_mid = self.message_id(session_id, idx)
            if (mid in hidden or legacy_mid in hidden) or idx <= baseline:
                continue
            mtype = _role.get(_msg_role(msg), "system")
            content = content_text(_msg_content(msg))
            ui = _msg_ui(msg)
            data: dict[str, Any] = {"content": content}
            files = _msg_files(msg)
            if files:
                data["files"] = files
            if ui.get("agents"):
                data["agents"] = ui["agents"]
            if ui.get("files_changed"):
                data["files_changed"] = ui["files_changed"]
            if ui.get("steps"):
                data["steps"] = ui["steps"]
            if ui.get("plan_path"):
                data["plan_path"] = ui["plan_path"]
            if ui.get("snapshot_restored"):
                data["snapshot_restored"] = ui["snapshot_restored"]
            if ui.get("partial_error"):
                data["partial_error"] = ui["partial_error"]
            out.append(
                {
                    "id": mid,
                    "session_id": session_id,
                    "type": mtype,
                    "role": mtype,
                    "content": content,
                    "data": data,
                    "seq": idx,
                    "time_created": 0,
                    "parts": [],
                    "is_compacted": False,
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
                    "is_compacted": True,
                },
            )
        return out

    async def _full_history(self, session_id: str) -> list[dict[str, Any]]:
        """未过滤的完整历史（含 hidden/基线之内），供 revert 计数与 fork 复制定位。

        直接读 checkpoint 原始消息（`jsonable` 会丢 message.id），
        id 用稳定 id，另附 `legacy_id` 以兼容旧位置 id。
        """
        _role = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
        raw = await self._raw_messages(session_id)
        out: list[dict[str, Any]] = []
        for idx, msg in enumerate(raw, start=1):
            mtype = _role.get(_msg_role(msg), "system")
            content = content_text(_msg_content(msg))
            out.append(
                {
                    "id": PlatformContext.stable_message_id(session_id, msg),
                    "legacy_id": self.message_id(session_id, idx),
                    "seq": idx,
                    "type": mtype,
                    "content": content,
                }
            )
        return out

    async def hide_message(self, session_id: str, message_id: str) -> bool:
        """逻辑删除单条消息；返回是否命中（稳定 id 或旧位置 id 均可）。"""
        sess = self.sessions.get(session_id)
        if sess is None or not message_id:
            return False
        if message_id.endswith("_compact"):
            sess.hidden_ids.add(message_id)
            sess.updated_at = time.time()
            self.sessions.persist(session_id)
            return True
        history = await self._full_history(session_id)
        known = {e["id"] for e in history} | {e.get("legacy_id") for e in history}
        if message_id not in known:
            return False
        sess.hidden_ids.add(message_id)
        sess.updated_at = time.time()
        self.sessions.persist(session_id)  # 逻辑删除跨重启存活
        return True

    async def revert_session(self, session_id: str, message_id: str) -> dict[str, Any]:
        """撤回 `message_id` 及其之后的所有消息（逻辑隐藏，保留 checkpoint）。

        以稳定消息 id 定位（旧位置 id 兼容）；返回 `{deleted, messages}`。
        """
        sess = self.sessions.get(session_id)
        if sess is None:
            return {"deleted": 0, "messages": []}
        history = await self._full_history(session_id)
        target: int | None = None
        for entry in history:
            if message_id in (entry["id"], entry.get("legacy_id")):
                target = int(entry["seq"])
                break
        if target is None:
            return {"deleted": 0, "messages": await self.session_messages(session_id)}
        deleted = 0
        for entry in history:
            if entry["seq"] < target:
                continue
            if entry["id"] not in sess.hidden_ids:
                sess.hidden_ids.add(entry["id"])
                deleted += 1
            legacy = entry.get("legacy_id")
            if legacy:
                sess.hidden_ids.add(legacy)
        # 压缩基线之后被撤回 → 基线回退到撤回点之前
        if target - 1 < sess.compact_baseline_seq:
            sess.compact_baseline_seq = max(0, target - 1)
        sess.updated_at = time.time()
        self.sessions.persist(session_id)  # 撤回集跨重启存活
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
        self.sessions.persist(session_id)  # 压缩基线跨重启存活
        return {"compacted": True, "baseline": sess.compact_baseline_seq}

    async def copy_messages_to_thread(
        self, src_id: str, dst_id: str, up_to_id: str | None = None
    ) -> int:
        """把 src 的可见消息复制到 dst 的 checkpoint（fork 用）。返回复制条数。

        `up_to_id` 为稳定消息 id（含）；找不到时复制全部可见消息。
        复制时保留原 message id，保证分叉后的消息与源会话对齐。
        """
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        msgs = await self.session_messages(src_id)
        raw = await self._raw_messages(src_id)

        def _src_content(m: dict[str, Any]) -> Any:
            """取原始 content：分块消息保留 image_url（`session_messages` 会压成文本）。"""
            seq = m.get("seq")
            if isinstance(seq, int) and 1 <= seq <= len(raw):
                return _msg_content(raw[seq - 1])
            return m.get("content", "")

        if up_to_id is not None:
            kept: list[dict[str, Any]] = []
            for m in msgs:
                kept.append(m)
                seq = m.get("seq")
                legacy = (
                    self.message_id(src_id, int(seq))
                    if isinstance(seq, int) and seq > 0
                    else None
                )
                if m["id"] == up_to_id or legacy == up_to_id:
                    break
            msgs = kept
        lc_msgs: list[Any] = []
        for m in msgs:
            content = m.get("content", "")
            files = (m.get("data") or {}).get("files") or []
            mid = m.get("id")
            if m["type"] == "user":
                lc_msg: Any = HumanMessage(content=_src_content(m), id=mid)
                if files:
                    lc_msg.additional_kwargs["files"] = _files_payload(files)
                lc_msgs.append(lc_msg)
            elif m["type"] == "assistant":
                lc_msgs.append(AIMessage(content=content, id=mid))
            elif m["type"] == "system":
                lc_msgs.append(SystemMessage(content=content, id=mid))
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
        await self.copy_messages_to_thread(session_id, forked.id, up_to_id=message_id)
        return forked

    def session_children(self, session_id: str) -> list[Session]:
        """按 parent_id 返回直接子会话（fork 分支）。"""
        try:  # 重启后子会话行先水合进内存（list() 无 user 维度，单独按 parent 补齐）
            from .session import repository as repo

            self.sessions.hydrate_rows(repo.list_children(session_id))
        except Exception:  # noqa: BLE001
            logger.debug("children 水合失败（库不可用时忽略）", exc_info=True)
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
                self.remember_approved_root(session_id, str(raw_path))
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
            self.remember_approved_root(session_id, str(raw_path))
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
    checkpointer: Any = None,
    skill_loader: Any | None = None,
) -> PlatformContext:
    """构建运行时上下文（默认装配 default_registry + 主图）。

    `checkpointer`：图检查点（生产为 `graph.checkpoint.get_checkpointer()` 的持久化
    AsyncSqliteSaver；None → build_main_graph 默认 MemorySaver，进程内不落盘）。
    """
    if registry is None:
        registry, vfs = default_registry(vfs)
    elif vfs is None:
        vfs = VirtualFileSystem()

    # 技能接线（1+2）：已启用技能 → `load_skill_*` 工具（模型自发现）；
    # 技能清单并入 system prompt；`/技能名` 由 ctx.run/stream 在轮次入口强制加载。
    skills_catalog_text = ""
    if skill_loader is not None:
        from .skills.tools import make_skill_tools, skills_catalog

        for fn, name, desc in make_skill_tools(skill_loader):
            try:
                registry.register_function(fn, name=name, description=desc, scope="global")
            except ValueError:  # 与内置工具重名：保留内置
                logger.warning("skill tool name conflict, skipped: %s", name)
        skills_catalog_text = skills_catalog(skill_loader)

    graph = build_main_graph(
        model=model, registry=registry, vfs=vfs, retrieve=retrieve, reranker=reranker,
        checkpointer=checkpointer, skills_catalog=skills_catalog_text,
    )
    # 并发上限统一读 Settings（与 session.db 连接池 sizing 同源）；
    # 此前直接 os.environ.get("MAX_CONCURRENT_AGENTS") 会造成双真相源漂移。
    from config import settings

    max_concurrent = max(1, int(settings.max_concurrent_agents))
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
        skill_loader=skill_loader,
    )


def get_context(request: Request) -> PlatformContext:
    """FastAPI 依赖：取出挂在 app.state 上的平台上下文。"""
    ctx = getattr(request.app.state, "platform", None)
    if ctx is None:  # pragma: no cover - 启动配置错误
        raise RuntimeError("PlatformContext 未初始化（检查 lifespan）")
    return ctx
