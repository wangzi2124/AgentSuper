"""Task state persistence with a SQLite/MySQL/PostgreSQL backend.

Tracks task execution state across the agent loop, enabling:
- Step counting for max_steps enforcement
- Compaction history tracking
- Crash recovery (resume from last checkpoint)

sqlite：data/tasks.db（每线程复用一条连接，WAL + busy_timeout）。
非 sqlite：统一后端门面（app.storage.backends），schema 由 Alembic 迁移链管理。
"""

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.storage import backends, schema as storage_schema

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "tasks.db"

# 已完成/失败任务的保留时间窗口（天），防止 tasks 表无界增长
_TASK_RETENTION_DAYS = 7

# 每线程复用一条连接（agent 每步 save() 高频调用，避免反复建连）；
# sqlite：WAL + busy_timeout 解决多会话并发写导致的 `database is locked`；
# 非 sqlite：同样 thread-local 复用统一后端门面连接。
_thread_local = threading.local()

_SCHEMA = storage_schema.derive_sqlite_ddl(storage_schema.TASK_TABLES)


def _get_db():
    """Get current thread's task database connection (reused, not reopened)."""
    if not backends.is_sqlite():
        return backends.thread_local_connect()
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = backends.make_sqlite_conn(
            DB_PATH,
            wal=True,
            busy_timeout=30000,
            schema_ddl=_SCHEMA,
        )
        conn.commit()
        _thread_local.conn = conn
    return conn


def cleanup_old_tasks() -> None:
    """清理超过保留时间的已完成/失败任务记录。

    updated_at 统一存 UTC（ISO-8601 含时区偏移）。
    - sqlite：datetime() 函数换算比较（与 datetime('now') 对齐）。
    - mysql / postgresql：用同一 ISO-8601 格式的 Python 截止串做字典序比较
      （任务写入方统一使用 datetime.now(timezone.utc).isoformat()，格式一致）。
    """
    try:
        conn = _get_db()
        if backends.is_sqlite():
            conn.execute(
                "DELETE FROM tasks WHERE status IN ('completed', 'failed') "
                "AND datetime(updated_at) < datetime('now', ?)",
                (f"-{_TASK_RETENTION_DAYS} days",),
            )
        else:
            cutoff = (
                datetime.now(timezone.utc)
                - timedelta(days=_TASK_RETENTION_DAYS)
            ).isoformat()
            conn.execute(
                "DELETE FROM tasks WHERE status IN ('completed', 'failed') "
                "AND updated_at < ?",
                (cutoff,),
            )
        conn.commit()
    except Exception as e:
        logger.warning("Failed to cleanup old tasks: %s", e)


class TaskState:
    """Persistent task execution state.

    Each user message that triggers a full agent loop creates a TaskState.
    It tracks progress and enables compaction/resume.
    """

    def __init__(
        self,
        task_id: str | None = None,
        conversation_id: str = "",
        status: str = "running",
        step: int = 0,
        total_tokens: int = 0,
        last_compaction_step: int = 0,
        tool_calls_count: int = 0,
    ):
        self.task_id = task_id or f"task_{uuid.uuid4().hex}"
        self.conversation_id = conversation_id
        self.status = status
        self.step = step
        self.total_tokens = total_tokens
        self.last_compaction_step = last_compaction_step
        self.tool_calls_count = tool_calls_count
        # 统一存 UTC（与 SQLite datetime('now') 同为 UTC，清理比较不再受本地时区影响）
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def save(self):
        """Persist task state to SQLite."""
        conn = _get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO tasks (id, conversation_id, status, step, total_tokens, "
            "last_compaction_step, tool_calls_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "status=excluded.status, step=excluded.step, total_tokens=excluded.total_tokens, "
            "last_compaction_step=excluded.last_compaction_step, "
            "tool_calls_count=excluded.tool_calls_count, updated_at=excluded.updated_at",
            (
                self.task_id, self.conversation_id, self.status, self.step,
                self.total_tokens, self.last_compaction_step, self.tool_calls_count,
                self.created_at, now,
            ),
        )
        conn.commit()
        self.updated_at = now

    def increment_step(self):
        """Increment the step counter and persist."""
        self.step += 1
        self.save()

    def add_tokens(self, count: int):
        """Add token count and persist."""
        self.total_tokens += count
        self.save()

    def increment_tool_calls(self, count: int = 1):
        """Increment the tool-call counter (by tool executions per round) and persist."""
        self.tool_calls_count += count
        self.save()

    def record_compaction(self):
        """Record that compaction happened at current step."""
        self.last_compaction_step = self.step
        self.save()

    def mark_completed(self):
        """Mark task as completed."""
        self.status = "completed"
        self.save()

    def mark_failed(self, error: str = ""):
        """Mark task as failed."""
        self.status = "failed"
        self.save()
        logger.error("Task %s failed: %s", self.task_id, error)

    def to_dict(self) -> dict:
        """Serialize task state for event emission."""
        return {
            "task_id": self.task_id,
            "conversation_id": self.conversation_id,
            "status": self.status,
            "step": self.step,
            "total_tokens": self.total_tokens,
            "tool_calls_count": self.tool_calls_count,
        }

    @classmethod
    def load(cls, task_id: str) -> Optional["TaskState"]:
        """Load a task state from SQLite."""
        conn = _get_db()
        row = conn.execute(
            "SELECT id, conversation_id, status, step, total_tokens, "
            "last_compaction_step, tool_calls_count, created_at "
            "FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None
        return cls(
            task_id=row[0], conversation_id=row[1], status=row[2],
            step=row[3], total_tokens=row[4], last_compaction_step=row[5],
            tool_calls_count=row[6],
        )

    @classmethod
    def list_by_conversation(cls, conversation_id: str) -> list["TaskState"]:
        """List all tasks for a conversation, newest first."""
        conn = _get_db()
        rows = conn.execute(
            "SELECT id, conversation_id, status, step, total_tokens, "
            "last_compaction_step, tool_calls_count, created_at "
            "FROM tasks WHERE conversation_id = ? ORDER BY created_at DESC",
            (conversation_id,),
        ).fetchall()
        return [
            cls(
                task_id=r[0], conversation_id=r[1], status=r[2],
                step=r[3], total_tokens=r[4], last_compaction_step=r[5],
                tool_calls_count=r[6],
            )
            for r in rows
        ]
