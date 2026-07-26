"""Task state persistence with SQLite.

Tracks task execution state across the agent loop, enabling:
- Step counting for max_steps enforcement
- Compaction history tracking
- Crash recovery (resume from last checkpoint)
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "tasks.db"


def _get_db() -> sqlite3.Connection:
    """Get task database connection and ensure schema exists."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tasks ("
        "  id TEXT PRIMARY KEY,"
        "  conversation_id TEXT NOT NULL,"
        "  status TEXT NOT NULL DEFAULT 'running',"
        "  step INTEGER NOT NULL DEFAULT 0,"
        "  total_tokens INTEGER NOT NULL DEFAULT 0,"
        "  last_compaction_step INTEGER NOT NULL DEFAULT 0,"
        "  tool_calls_count INTEGER NOT NULL DEFAULT 0,"
        "  created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        "  updated_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    conn.commit()
    return conn


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
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()

    def save(self):
        """Persist task state to SQLite."""
        conn = _get_db()
        try:
            now = datetime.now().isoformat()
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
        finally:
            conn.close()

    def increment_step(self):
        """Increment the step counter and persist."""
        self.step += 1
        self.save()

    def add_tokens(self, count: int):
        """Add token count and persist."""
        self.total_tokens += count
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
        try:
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
        finally:
            conn.close()

    @classmethod
    def list_by_conversation(cls, conversation_id: str) -> list["TaskState"]:
        """List all tasks for a conversation, newest first."""
        conn = _get_db()
        try:
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
        finally:
            conn.close()
