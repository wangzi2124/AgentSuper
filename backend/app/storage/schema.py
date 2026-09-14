"""统一存储 schema（单一声明来源）。

四个 SQLite 子系统的表全部在此以 SQLAlchemy Core Table 声明，作为单一事实来源：
- `session.db`   → projects / workspaces / sessions / session_messages / message_parts /
                    session_context_epoch / session_inputs / session_tasks
- `model_catalog.db` → catalog_settings / providers / catalog_entries
- `chapter_store.db` → chapters
- `tasks.db`     → tasks

用途：
- **sqlite**：`derive_sqlite_ddl()` 从 metadata 编译 `CREATE TABLE IF NOT EXISTS` /
  `CREATE INDEX IF NOT EXISTS`，替代各子系统手写的 _SCHEMA 字符串（sqlite 建表零回归，
  旧库的 `_ensure_column` 兼容补列逻辑保留）。
- **mysql / postgresql**：Alembic 初始迁移 `0001_initial` 直接 `metadata.create_all(...)`，
  迁移链与 metadata 同源，后续 schema 演进走 revisions。
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import sqlite
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.schema import CreateIndex, CreateTable

metadata = MetaData()

# ── session.db ──────────────────────────────────────────────────────────────
# 端口直流类型：主键一律 String(n)（MySQL TEXT 不能做主键/被索引）；
# 大数据列（消息 data / 纪元快照 / 压缩基线 / 输入 prompt）用 LONGTEXT 变体（MySQL）。

projects = Table(
    "projects", metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(512), nullable=False, server_default=""),
    Column("root", Text(), nullable=False),
    Column("vcs", String(64), nullable=False, server_default=""),
    Column("time_created", BigInteger, nullable=False),
    Column("time_updated", BigInteger, nullable=False),
)

workspaces = Table(
    "workspaces", metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), ForeignKey("projects.id", ondelete="CASCADE"),
           nullable=False),
    Column("name", String(512), nullable=False, server_default=""),
    Column("time_created", BigInteger, nullable=False),
)

sessions = Table(
    "sessions", metadata,
    Column("id", String(64), primary_key=True),
    Column("slug", String(64), nullable=False),
    Column("version", String(16), nullable=False, server_default="1"),
    Column("user_id", String(64), nullable=False),
    Column("project_id", String(64), ForeignKey("projects.id", ondelete="CASCADE"),
           nullable=False),
    Column("workspace_id", String(64), ForeignKey("workspaces.id", ondelete="SET NULL"),
           nullable=True),
    Column("parent_id", String(64), ForeignKey("sessions.id", ondelete="CASCADE"),
           nullable=True),
    Column("directory", Text(), nullable=False),
    Column("path", Text(), nullable=False),
    Column("title", Text(), nullable=False),
    Column("agent", Text(), nullable=True),
    Column("model", Text(), nullable=True),
    Column("kind", String(32), nullable=False, server_default="chat"),
    Column("status", String(32), nullable=False, server_default="idle"),
    Column("cost", Float, nullable=False, server_default=text("0")),
    Column("tokens_input", BigInteger, nullable=False, server_default=text("0")),
    Column("tokens_output", BigInteger, nullable=False, server_default=text("0")),
    Column("tokens_cache_read", BigInteger, nullable=False, server_default=text("0")),
    Column("tokens_cache_write", BigInteger, nullable=False, server_default=text("0")),
    Column("tokens_reasoning", BigInteger, nullable=False, server_default=text("0")),
    Column("time_created", BigInteger, nullable=False),
    Column("time_updated", BigInteger, nullable=False),
    Column("time_compacted", BigInteger, nullable=True),
    Column("time_archived", BigInteger, nullable=True),
    Index("idx_sessions_user", "user_id"),
    Index("idx_sessions_project", "project_id"),
    Index("idx_sessions_parent", "parent_id"),
    Index("idx_sessions_updated", "time_updated"),
)

session_messages = Table(
    "session_messages", metadata,
    Column("seq", Integer, primary_key=True),
    Column("id", String(64), nullable=False),
    Column("session_id", String(64), ForeignKey("sessions.id", ondelete="CASCADE"),
           primary_key=True),
    Column("type", String(32), nullable=False),
    Column("data", Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column("time_created", BigInteger, nullable=False),
    UniqueConstraint("session_id", "id"),
    Index("idx_messages_session_time", "session_id", "time_created"),
)

message_parts = Table(
    "message_parts", metadata,
    Column("id", String(64), primary_key=True),
    Column("session_id", String(64), ForeignKey("sessions.id", ondelete="CASCADE"),
           primary_key=True),
    Column("message_id", String(64), nullable=False),
    Column("type", String(32), nullable=False),
    Column("data", Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column("time_created", BigInteger, nullable=False),
    Index("idx_parts_message", "message_id"),
)

session_context_epoch = Table(
    "session_context_epoch", metadata,
    Column("session_id", String(64), ForeignKey("sessions.id", ondelete="CASCADE"),
           primary_key=True),
    Column("baseline", Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column("baseline_seq", Integer, nullable=False),
    Column("snapshot", Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column("time_created", BigInteger, nullable=False),
    Column("time_updated", BigInteger, nullable=False),
)

session_inputs = Table(
    "session_inputs", metadata,
    Column("id", String(64), primary_key=True),
    Column("session_id", String(64), ForeignKey("sessions.id", ondelete="CASCADE"),
           primary_key=True),
    Column("prompt", Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column("delivery", String(16), nullable=False, server_default="steer"),
    Column("admitted_seq", Integer, nullable=False),
    Column("promoted_seq", Integer, nullable=True),
    Column("time_created", BigInteger, nullable=False),
)

session_tasks = Table(
    "session_tasks", metadata,
    Column("id", String(64), primary_key=True),
    Column("session_id", String(64), ForeignKey("sessions.id", ondelete="CASCADE"),
           nullable=False),
    Column("parent_task_id", String(64), ForeignKey("session_tasks.id", ondelete="CASCADE"),
           nullable=True),
    Column("status", String(32), nullable=False, server_default="running"),
    Column("step", Integer, nullable=False, server_default=text("0")),
    Column("total_tokens", BigInteger, nullable=False, server_default=text("0")),
    Column("tool_calls_count", Integer, nullable=False, server_default=text("0")),
    Column("time_created", BigInteger, nullable=False),
    Column("time_updated", BigInteger, nullable=False),
    Index("idx_tasks_session", "session_id"),
)

# ── model_catalog.db ────────────────────────────────────────────────────────

catalog_settings = Table(
    "catalog_settings", metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text(), nullable=False),
)

providers = Table(
    "providers", metadata,
    Column("name", String(255), primary_key=True),
    Column("label", String(255), nullable=False, server_default=""),
    Column("api_base", Text(), nullable=False),
    Column("api_key", Text(), nullable=False),
    Column("enabled", Integer, nullable=False, server_default=text("1")),
    Column("data", Text(), nullable=False),
)

catalog_entries = Table(
    "catalog_entries", metadata,
    Column("id", String(255), primary_key=True),
    Column("kind", String(16), nullable=False),
    Column("data", Text(), nullable=False),
)

# ── chapter_store.db ────────────────────────────────────────────────────────
# chapter_title 需要被索引 → VARCHAR(768)（MySQL utf8mb4 索引限长 3072 字节）

chapters = Table(
    "chapters", metadata,
    Column("id", String(64), primary_key=True),
    Column("document_id", String(255), nullable=False),
    Column("document_filename", Text(), nullable=False),
    Column("chapter_number", Integer, nullable=True),
    Column("chapter_title", String(768), nullable=False),
    Column("summary", Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
    Column("parent_chunk_id", String(255), nullable=True),
    Index("idx_chapters_doc", "document_id"),
    Index("idx_chapters_title", "chapter_title"),
    Index("idx_chapters_number", "chapter_number"),
)

# ── tasks.db ────────────────────────────────────────────────────────────────
# created_at/updated_at 由代码显式写入（UTC ISO-8601 字符串），故不设 server default。

tasks = Table(
    "tasks", metadata,
    Column("id", String(64), primary_key=True),
    Column("conversation_id", String(255), nullable=False),
    Column("status", String(32), nullable=False, server_default="running"),
    Column("step", Integer, nullable=False, server_default=text("0")),
    Column("total_tokens", BigInteger, nullable=False, server_default=text("0")),
    Column("last_compaction_step", Integer, nullable=False, server_default=text("0")),
    Column("tool_calls_count", Integer, nullable=False, server_default=text("0")),
    Column("created_at", String(64), nullable=False),
    Column("updated_at", String(64), nullable=False),
)

# 各子系统在 sqlite 路径下负责建表/建索引的表名集合
SESSION_TABLES = (
    projects.name, workspaces.name, sessions.name, session_messages.name,
    message_parts.name, session_context_epoch.name, session_inputs.name,
    session_tasks.name,
)
CATALOG_TABLES = (catalog_settings.name, providers.name, catalog_entries.name)
CHAPTER_TABLES = (chapters.name,)
TASK_TABLES = (tasks.name,)


def derive_sqlite_ddl(table_names: tuple[str, ...]) -> str:
    """从 metadata 编译 sqlite 建表/建索引 DDL（CREATE TABLE/INDEX IF NOT EXISTS）。

    替代各子系统手写的 _SCHEMA 字符串 —— 表结构声明只有 metadata 一处，
    Alembic 初始迁移与 sqlite 建表路径同源。
    """
    dialect = sqlite.dialect()
    statements: list[str] = []
    for t in metadata.sorted_tables:
        if t.name in table_names:
            statements.append(str(CreateTable(t, if_not_exists=True).compile(dialect=dialect)))
            for idx in t.indexes:
                statements.append(
                    str(CreateIndex(idx, if_not_exists=True).compile(dialect=dialect))
                )
    return ";\n".join(statements)