"""form revision for durable questions

Revision ID: 0009_form_revision
Revises: 0008_provider_presets
Create Date: 2026-11-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_form_revision"
down_revision = "0008_provider_presets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "session_forms",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("session_forms", "revision")
