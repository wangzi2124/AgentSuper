"""event outbox

Revision ID: 0005_event_outbox
Revises: 0004_web_chat_v2
Create Date: 2026-11-07

sqlite 后端由连接时 derive_sqlite_ddl 建表；
本迁移只服务 mysql / postgresql。
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_event_outbox"
down_revision = "0004_web_chat_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_outbox",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.Column("created", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("aggregate_id", "seq"),
    )
    op.create_index(
        "idx_event_outbox_aggregate",
        "event_outbox",
        ["aggregate_id", "seq"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_event_outbox_aggregate", table_name="event_outbox")
    op.drop_table("event_outbox")
