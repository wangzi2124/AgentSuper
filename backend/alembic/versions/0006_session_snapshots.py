"""persist session turn snapshots

Revision ID: 0006_session_snapshots
Revises: 0005_event_outbox
Create Date: 2026-11-07
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_session_snapshots"
down_revision = "0005_event_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_snapshots",
        sa.Column("session_id", sa.String(64), sa.ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("message_id", sa.String(64), primary_key=True),
        sa.Column("descriptor", sa.Text(), nullable=False),
        sa.Column("time_created", sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("session_snapshots")
