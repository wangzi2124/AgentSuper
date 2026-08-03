"""Session 数据访问层。

对齐 opencode：
- session.ts / index.ts 的 CRUD 与查询（list 按 project/workspace 过滤）
- sql.ts 的归一化表
- 级联删除子会话（session.ts:remove）
- SessionProjector 的追加写语义（append-only 日志）
"""

import json
import sqlite3
import time
import uuid
from typing import Any, Optional

from .db import _get_db
from .models import ContextEpoch, Message, Part, ProjectInfo, SessionInfo

_DEFAULT_USER = "anonymous"


def _row_to_session(row: sqlite3.Row) -> SessionInfo:
    data = dict(row)
    return SessionInfo(
        id=data["id"],
        slug=data["slug"],
        user_id=data["user_id"],
        project_id=data["project_id"],
        workspace_id=data["workspace_id"],
        parent_id=data["parent_id"],
        directory=data["directory"],
        path=data["path"],
        title=data["title"],
        agent=data["agent"],
        model=json.loads(data["model"]) if data["model"] else None,
        kind=data["kind"],
        status=data["status"],
        cost=data["cost"],
        tokens_input=data["tokens_input"],
        tokens_output=data["tokens_output"],
        tokens_cache_read=data["tokens_cache_read"],
        tokens_cache_write=data["tokens_cache_write"],
        time_created=data["time_created"],
        time_updated=data["time_updated"],
        time_compacted=data["time_compacted"],
        time_archived=data["time_archived"],
    )


class SessionNotFound(Exception):
    pass


class Forbidden(Exception):
    pass


def resolve_project(root: str, name: str = "", vcs: str = "") -> ProjectInfo:
    """按目录解析项目（对齐 opencode ProjectV2.resolve：git root 探测）。

    当前简化实现：root 的 sha1 即 project_id，root 由调用方（deps.py）探测。
    """
    import hashlib

    pid = hashlib.sha1(root.encode("utf-8")).hexdigest()[:16]
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO projects (id, name, root, vcs, time_created, time_updated) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, vcs=excluded.vcs, time_updated=excluded.time_updated",
            (pid, name, root, vcs, int(time.time()), int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()
    return ProjectInfo(id=pid, name=name, root=root, vcs=vcs)


def get_project(project_id: str) -> Optional[ProjectInfo]:
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            return None
        return ProjectInfo(id=row["id"], name=row["name"], root=row["root"], vcs=row["vcs"])
    finally:
        conn.close()


def create_session(
    user_id: str,
    project_id: str,
    directory: str,
    *,
    path: str = "",
    parent_id: Optional[str] = None,
    agent: Optional[str] = None,
    model: Optional[Any] = None,
    kind: str = "chat",
    title: Optional[str] = None,
) -> SessionInfo:
    now = int(time.time() * 1000)
    sid = f"ses_{uuid.uuid4().hex[:24]}"
    slug = uuid.uuid4().hex[:8]
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO sessions (id, slug, version, user_id, project_id, parent_id, directory, path,"
            " title, agent, model, kind, status, cost, tokens_input, tokens_output,"
            " tokens_cache_read, tokens_cache_write, time_created, time_updated)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid, slug, "1", user_id, project_id, parent_id, directory, path,
                title or f"新会话 - {time.strftime('%Y-%m-%d %H:%M:%S')}",
                agent, json.dumps(model) if model else None, kind, "idle",
                0, 0, 0, 0, 0, now, now,
            ),
        )
        conn.commit()
        return get_session(sid)
    finally:
        conn.close()


def get_session(session_id: str) -> Optional[SessionInfo]:
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return _row_to_session(row) if row else None
    finally:
        conn.close()


def require_session(session_id: str) -> SessionInfo:
    session = get_session(session_id)
    if not session:
        raise SessionNotFound(session_id)
    return session


def list_sessions(
    user_id: str,
    *,
    project_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    roots_only: bool = False,
    search: Optional[str] = None,
    archived: Optional[bool] = False,
    limit: int = 100,
) -> list[SessionInfo]:
    """列出用户可见的会话（对齐 opencode Session.list 的 project 过滤）。"""
    sql = "SELECT * FROM sessions WHERE user_id = ?"
    params: list[Any] = [user_id]
    if project_id:
        sql += " AND project_id = ?"
        params.append(project_id)
    if workspace_id:
        sql += " AND workspace_id = ?"
        params.append(workspace_id)
    if roots_only:
        sql += " AND parent_id IS NULL"
    if search:
        sql += " AND title LIKE ?"
        params.append(f"%{search}%")
    if archived is False:
        sql += " AND time_archived IS NULL"
    sql += " ORDER BY time_updated DESC LIMIT ?"
    params.append(limit)
    conn = _get_db()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_session(r) for r in rows]
    finally:
        conn.close()


def update_session(session_id: str, **fields: Any) -> SessionInfo:
    allowed = {"title", "archived", "agent", "model", "status", "cost",
               "tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_write",
               "time_compacted", "time_archived"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "model":
            value = json.dumps(value) if value else None
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return require_session(session_id)
    sets.append("time_updated = ?")
    params.append(int(time.time() * 1000))
    params.append(session_id)
    conn = _get_db()
    try:
        conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()
    return require_session(session_id)


def list_children(parent_id: str) -> list[SessionInfo]:
    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM sessions WHERE parent_id = ?", (parent_id,)).fetchall()
        return [_row_to_session(r) for r in rows]
    finally:
        conn.close()


def remove_session(session_id: str) -> None:
    """递归删除子会话；外键 ON DELETE CASCADE 兜底清理消息/部件/纪元。"""
    conn = _get_db()
    try:
        stack = [session_id]
        while stack:
            current = stack.pop()
            for child in conn.execute("SELECT id FROM sessions WHERE parent_id = ?", (current,)).fetchall():
                stack.append(child["id"])
            conn.execute("DELETE FROM sessions WHERE id = ?", (current,))
        conn.commit()
    finally:
        conn.close()


# ── 消息日志（append-only）───────────────────────────────────────────────

def _next_seq(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM session_messages WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return int(row["n"])


def append_message(session_id: str, msg_type: str, data: dict[str, Any]) -> Message:
    """追加一条消息到事件日志并返回（对齐 SessionProjector 的追加写）。"""
    conn = _get_db()
    try:
        seq = _next_seq(conn, session_id)
        mid = f"msg_{uuid.uuid4().hex[:24]}"
        now = int(time.time() * 1000)
        conn.execute(
            "INSERT INTO session_messages (seq, id, session_id, type, data, time_created)"
            " VALUES (?,?,?,?,?,?)",
            (seq, mid, session_id, msg_type, json.dumps(data, ensure_ascii=False), now),
        )
        conn.execute(
            "UPDATE sessions SET time_updated = ? WHERE id = ?", (now, session_id)
        )
        conn.commit()
        return Message(id=mid, session_id=session_id, type=msg_type, data=data, seq=seq, time_created=now)
    finally:
        conn.close()


def list_messages(session_id: str, after_seq: int = 0, limit: Optional[int] = None) -> list[Message]:
    conn = _get_db()
    try:
        sql = ("SELECT * FROM session_messages WHERE session_id = ? AND seq > ? ORDER BY seq")
        params: list[Any] = [session_id, after_seq]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [
            Message(
                id=r["id"], session_id=r["session_id"], type=r["type"],
                data=json.loads(r["data"]), seq=r["seq"], time_created=r["time_created"],
            )
            for r in rows
        ]
    finally:
        conn.close()


def latest_seq(session_id: str) -> int:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS n FROM session_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["n"])
    finally:
        conn.close()


def latest_compaction_seq(session_id: str) -> Optional[int]:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT MAX(seq) AS n FROM session_messages WHERE session_id = ? AND type = 'compaction'",
            (session_id,),
        ).fetchone()
        return int(row["n"]) if row and row["n"] is not None else None
    finally:
        conn.close()


def append_part(session_id: str, message_id: str, part_type: str, data: dict[str, Any]) -> Part:
    conn = _get_db()
    try:
        pid = f"pt_{uuid.uuid4().hex[:24]}"
        now = int(time.time() * 1000)
        conn.execute(
            "INSERT INTO message_parts (id, session_id, message_id, type, data, time_created)"
            " VALUES (?,?,?,?,?,?)",
            (pid, session_id, message_id, part_type, json.dumps(data, ensure_ascii=False), now),
        )
        conn.commit()
        return Part(id=pid, session_id=session_id, message_id=message_id, type=part_type, data=data, time_created=now)
    finally:
        conn.close()


def list_parts(message_id: str) -> list[Part]:
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM message_parts WHERE message_id = ? ORDER BY time_created", (message_id,)
        ).fetchall()
        return [
            Part(id=r["id"], session_id=r["session_id"], message_id=r["message_id"],
                 type=r["type"], data=json.loads(r["data"]), time_created=r["time_created"])
            for r in rows
        ]
    finally:
        conn.close()


# ── 上下文纪元 ────────────────────────────────────────────────────────────

def get_epoch(session_id: str) -> Optional[ContextEpoch]:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM session_context_epoch WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return ContextEpoch(
            session_id=row["session_id"], baseline=row["baseline"],
            baseline_seq=row["baseline_seq"], snapshot=json.loads(row["snapshot"]),
        )
    finally:
        conn.close()


def upsert_epoch(session_id: str, baseline: str, baseline_seq: int, snapshot: dict[str, Any]) -> None:
    now = int(time.time() * 1000)
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO session_context_epoch (session_id, baseline, baseline_seq, snapshot, time_created, time_updated)"
            " VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET baseline=excluded.baseline,"
            " baseline_seq=excluded.baseline_seq, snapshot=excluded.snapshot, time_updated=excluded.time_updated",
            (session_id, baseline, baseline_seq, json.dumps(snapshot, ensure_ascii=False), now, now),
        )
        conn.commit()
    finally:
        conn.close()


# ── 输入队列（steer / queue）─────────────────────────────────────────────

def admit_input(session_id: str, prompt: dict[str, Any], delivery: str = "steer") -> str:
    """投递输入：入队并返回 input id（对齐 opencode SessionInput.admit）。"""
    conn = _get_db()
    try:
        iid = f"in_{uuid.uuid4().hex[:24]}"
        now = int(time.time() * 1000)
        admitted_seq = _next_seq(conn, session_id)
        conn.execute(
            "INSERT INTO session_inputs (id, session_id, prompt, delivery, admitted_seq, time_created)"
            " VALUES (?,?,?,?,?,?)",
            (iid, session_id, json.dumps(prompt, ensure_ascii=False), delivery, admitted_seq, now),
        )
        conn.commit()
        return iid
    finally:
        conn.close()


def promote_next(session_id: str) -> Optional[dict[str, Any]]:
    """提升下一条待执行输入（steer 优先于 queue），返回其 prompt。"""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM session_inputs WHERE session_id = ? AND promoted_seq IS NULL"
            " ORDER BY (delivery = 'steer') DESC, admitted_seq ASC LIMIT 1",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE session_inputs SET promoted_seq = ? WHERE session_id = ? AND id = ?",
            (row["admitted_seq"], session_id, row["id"]),
        )
        conn.commit()
        return {"id": row["id"], "prompt": json.loads(row["prompt"]), "delivery": row["delivery"]}
    finally:
        conn.close()


def has_pending(session_id: str, delivery: Optional[str] = None) -> bool:
    conn = _get_db()
    try:
        sql = "SELECT 1 FROM session_inputs WHERE session_id = ? AND promoted_seq IS NULL"
        params: list[Any] = [session_id]
        if delivery:
            sql += " AND delivery = ?"
            params.append(delivery)
        return conn.execute(sql + " LIMIT 1", params).fetchone() is not None
    finally:
        conn.close()


def count_pending(session_id: str) -> int:
    """当前会话队列中未执行的输入数（对齐 opencode queue depth）。"""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM session_inputs WHERE session_id = ? AND promoted_seq IS NULL",
            (session_id,),
        ).fetchone()
        return int(row["n"])
    finally:
        conn.close()
