"""add sessions UI-state columns (hidden_ids / compact_baseline_seq)

给 `sessions` 补平台层逻辑删除集与压缩基线两列：
`deps.SessionStore` 落库/水合用它们让「删除的消息 / 压缩基线」跨重启存活
（此前这两者只存内存 Session，重启即丢 → 删掉的消息复活）。

sqlite 后端由连接时 `_ensure_column` 幂等补列（见 app/session/db.py），
本迁移只服务 mysql / postgresql。

Revision ID: 0003_session_ui_state
Revises: 0002_chain_logs
Create Date: 2026-10-19
"""
from alembic import op

revision = "0003_session_ui_state"
down_revision = "0002_chain_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from sqlalchemy import Column, Integer, Text, text

    op.add_column(
        "sessions",
        Column("hidden_ids", Text(), nullable=False, server_default=text("'[]'")),
    )
    op.add_column(
        "sessions",
        Column("compact_baseline_seq", Integer(), nullable=False, server_default=text("0")),
    )


def downgrade() -> None:
    op.drop_column("sessions", "compact_baseline_seq")
    op.drop_column("sessions", "hidden_ids")
