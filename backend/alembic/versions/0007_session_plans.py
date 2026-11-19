"""persist session todo plans

Revision ID: 0007_session_plans
Revises: 0006_session_snapshots
Create Date: 2026-11-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_session_plans"
down_revision = "0006_session_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_plans",
        sa.Column("session_id", sa.String(64), sa.ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("plan_id", sa.String(64), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tier", sa.String(32), nullable=True),
        sa.Column("steps", sa.Text(), nullable=False),
        sa.Column("time_created", sa.BigInteger(), nullable=False),
        sa.Column("time_updated", sa.BigInteger(), nullable=False),
    )
    op.create_index("idx_plans_session", "session_plans", ["session_id", "revision"])


def downgrade() -> None:
    op.drop_index("idx_plans_session", table_name="session_plans")
    op.drop_table("session_plans")
