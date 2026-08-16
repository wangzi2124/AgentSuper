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
from .ids import new_id
from .models import ContextEpoch, Message, Part, ProjectInfo, SessionInfo

_DEFAULT_USER = "anonymous"

# SQLite 变量上限默认 999；`IN (...)` 一次拼接超过会报 too many SQL variables。
# 长会话（含大量工具消息）的 message_id 集合可能超限，统一按批分片。
_SQLITE_MAX_VARS = 500


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


class MessageNotFound(Exception):
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
    kind: str = "multi-agent",
    title: Optional[str] = None,
    session_id: Optional[str] = None,
) -> SessionInfo:
    now = int(time.time() * 1000)
    sid = session_id or new_id("ses_")
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
    kind: Optional[str] = None,
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
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
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

def append_message(session_id: str, msg_type: str, data: dict[str, Any]) -> Message:
    """追加一条消息到事件日志并返回（对齐 SessionProjector 的追加写）。

    seq 在单条 INSERT 内原子计算（BEGIN IMMEDIATE 持有写锁），并发写者
    （coordinator 执行体 + multi-agent 直写 + compact/revert/fork 等）不会
    算出相同 seq 造成主键冲突或乱序。
    """
    conn = _get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        mid = new_id("msg_")
        now = int(time.time() * 1000)
        conn.execute(
            "INSERT INTO session_messages (seq, id, session_id, type, data, time_created)"
            " VALUES ((SELECT COALESCE(MAX(seq), 0) + 1 FROM session_messages WHERE session_id = ?),?,?,?,?,?)",
            (session_id, mid, session_id, msg_type, json.dumps(data, ensure_ascii=False), now),
        )
        seq = conn.execute(
            "SELECT seq FROM session_messages WHERE session_id = ? AND id = ?", (session_id, mid)
        ).fetchone()["seq"]
        conn.execute(
            "UPDATE sessions SET time_updated = ? WHERE id = ?", (now, session_id)
        )
        conn.commit()
        return Message(id=mid, session_id=session_id, type=msg_type, data=data, seq=int(seq), time_created=now)
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


def seq_of_message(session_id: str, message_id: str) -> Optional[int]:
    """按消息 id 查 seq（tail 起点水位定位用；id 为 UNIQUE(session_id, id)）。

    压缩时 epoch.snapshot 只落 tail_start_id（旧格式）时，用此函数反查 seq。
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT seq FROM session_messages WHERE session_id = ? AND id = ?",
            (session_id, message_id),
        ).fetchone()
        return int(row["seq"]) if row else None
    finally:
        conn.close()


def add_session_usage(session_id: str, input_tokens: int = 0, output_tokens: int = 0, cost: float = 0.0) -> None:
    """累加会话级 token/费用（对齐 sessions 表成本结算列）。"""
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE sessions SET tokens_input = tokens_input + ?, tokens_output = tokens_output + ?,"
            " cost = cost + ?, time_updated = ? WHERE id = ?",
            (int(input_tokens), int(output_tokens), float(cost), int(time.time() * 1000), session_id),
        )
        conn.commit()
    finally:
        conn.close()


def append_part(session_id: str, message_id: str, part_type: str, data: dict[str, Any]) -> Part:
    conn = _get_db()
    try:
        pid = new_id("prt_")
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
            "SELECT * FROM message_parts WHERE message_id = ? ORDER BY time_created, id", (message_id,)
        ).fetchall()
        return [
            Part(id=r["id"], session_id=r["session_id"], message_id=r["message_id"],
                 type=r["type"], data=json.loads(r["data"]), time_created=r["time_created"])
            for r in rows
        ]
    finally:
        conn.close()


def list_parts_for_messages(message_ids: list[str]) -> dict[str, list[Part]]:
    """按消息批量加载 parts（保持每消息内 (time_created, id) 排序）。

    分批查询：message_id 集合可能超过 SQLite 变量上限，按 _SQLITE_MAX_VARS 分片，
    避免长会话加载历史时报 too many SQL variables。
    """
    if not message_ids:
        return {}
    conn = _get_db()
    try:
        out: dict[str, list[Part]] = {}
        for i in range(0, len(message_ids), _SQLITE_MAX_VARS):
            batch = message_ids[i:i + _SQLITE_MAX_VARS]
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"SELECT * FROM message_parts WHERE message_id IN ({placeholders})"
                " ORDER BY message_id, time_created, id",
                batch,
            ).fetchall()
            for r in rows:
                p = Part(id=r["id"], session_id=r["session_id"], message_id=r["message_id"],
                         type=r["type"], data=json.loads(r["data"]), time_created=r["time_created"])
                out.setdefault(r["message_id"], []).append(p)
        return out
    finally:
        conn.close()


def update_part(session_id: str, part_id: str, data: dict[str, Any]) -> Optional[Part]:
    """就地更新一条 part 的 data（如 tool 从 running → completed）。"""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM message_parts WHERE session_id = ? AND id = ?",
            (session_id, part_id),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE message_parts SET data = ? WHERE session_id = ? AND id = ?",
            (json.dumps(data, ensure_ascii=False), session_id, part_id),
        )
        conn.commit()
        return Part(id=row["id"], session_id=row["session_id"], message_id=row["message_id"],
                    type=row["type"], data=data, time_created=row["time_created"])
    finally:
        conn.close()


def update_message(session_id: str, message_id: str, data: dict[str, Any]) -> Optional[Message]:
    """就地更新一条消息的 data（如 assistant 结算字段/最终答案回填）。

    消息日志仍是 append-only：本函数只更新既有行的 data，不新增 seq。
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM session_messages WHERE session_id = ? AND id = ?",
            (session_id, message_id),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE session_messages SET data = ? WHERE session_id = ? AND id = ?",
            (json.dumps(data, ensure_ascii=False), session_id, message_id),
        )
        conn.commit()
        return Message(id=row["id"], session_id=row["session_id"], type=row["type"],
                       data=data, seq=row["seq"], time_created=row["time_created"])
    finally:
        conn.close()


def delete_message(session_id: str, message_id: str) -> bool:
    """删除指定会话中的一条消息（对齐旧 delete_message 端点语义）。"""
    conn = _get_db()
    try:
        cur = conn.execute(
            "DELETE FROM session_messages WHERE session_id = ? AND id = ?",
            (session_id, message_id),
        )
        conn.execute("DELETE FROM message_parts WHERE session_id = ? AND message_id = ?",
                     (session_id, message_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def revert_to_message(session_id: str, message_id: str) -> int:
    """撤销到指定消息（对齐 opencode revert.ts）。

    删除该消息之后的所有消息及其 message_parts；若纪元水位越过撤销点则回滚，
    避免 history.load 把已被撤销的消息继续纳入模型视角。

    Returns:
        deleted 的消息条数。
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT seq FROM session_messages WHERE session_id = ? AND id = ?",
            (session_id, message_id),
        ).fetchone()
        if not row:
            raise MessageNotFound(message_id)
        target_seq = int(row["seq"])

        doomed = conn.execute(
            "SELECT id FROM session_messages WHERE session_id = ? AND seq > ?",
            (session_id, target_seq),
        ).fetchall()
        ids = [d["id"] for d in doomed]
        if ids:
            # 分批删除，避免 message_id 集合超过 SQLite 变量上限
            for i in range(0, len(ids), _SQLITE_MAX_VARS):
                batch = ids[i:i + _SQLITE_MAX_VARS]
                placeholders = ",".join("?" * len(batch))
                conn.execute(
                    f"DELETE FROM message_parts WHERE session_id = ? AND message_id IN ({placeholders})",
                    [session_id, *batch],
                )
            conn.execute(
                "DELETE FROM session_messages WHERE session_id = ? AND seq > ?",
                (session_id, target_seq),
            )

        # 回滚上下文纪元水位：baseline_seq 不得超过撤销后剩余的最新 compaction
        # 消息（无则归 0），保证被保留的历史仍对模型可见。
        rem_comp = conn.execute(
            "SELECT MAX(seq) AS n FROM session_messages"
            " WHERE session_id = ? AND type = 'compaction' AND seq <= ?",
            (session_id, target_seq),
        ).fetchone()
        new_base = int(rem_comp["n"]) if rem_comp and rem_comp["n"] is not None else 0
        epoch = conn.execute(
            "SELECT baseline_seq FROM session_context_epoch WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if epoch and int(epoch["baseline_seq"]) > new_base:
            conn.execute(
                "UPDATE session_context_epoch SET baseline_seq = ?, time_updated = ? WHERE session_id = ?",
                (new_base, int(time.time() * 1000), session_id),
            )
        now = int(time.time() * 1000)
        conn.execute("UPDATE sessions SET time_updated = ? WHERE id = ?", (now, session_id))
        conn.commit()
        return len(ids)
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
    """投递输入：入队并返回 input id（对齐 opencode SessionInput.admit）。

    admitted_seq 在单条 INSERT 内原子计算（同 session_messages 的 seq 语义）。
    """
    conn = _get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        iid = new_id("in_")
        now = int(time.time() * 1000)
        conn.execute(
            "INSERT INTO session_inputs (id, session_id, prompt, delivery, admitted_seq, time_created)"
            " VALUES (?,?,?,?,(SELECT COALESCE(MAX(seq), 0) + 1 FROM session_messages WHERE session_id = ?),?)",
            (iid, session_id, json.dumps(prompt, ensure_ascii=False), delivery, session_id, now),
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


def clear_inputs(session_id: str) -> int:
    """清除该会话全部未执行/已入队输入（revert 撤销后防止内容复活）。"""
    conn = _get_db()
    try:
        cur = conn.execute("DELETE FROM session_inputs WHERE session_id = ?", (session_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
