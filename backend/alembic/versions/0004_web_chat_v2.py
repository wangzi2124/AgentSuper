"""web chat v2 persistence

Revision ID: 0004_web_chat_v2
Revises: 0003_session_ui_state
Create Date: 2026-11-07

sqlite 后端由连接时迁移（app/session/migrations.py）执行；
本迁移只服务 mysql / postgresql。
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_web_chat_v2"
down_revision = "0003_session_ui_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("outcome", sa.String(16), nullable=True))
    op.add_column("sessions", sa.Column("metadata", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("sessions", sa.Column("revert_state", sa.Text(), nullable=True))
    op.add_column("sessions", sa.Column("time_busy", sa.BigInteger(), nullable=True))
    op.add_column("sessions", sa.Column("time_idle", sa.BigInteger(), nullable=True))
    op.add_column("sessions", sa.Column("time_viewed", sa.BigInteger(), nullable=True))

    op.add_column("session_messages", sa.Column("time_completed", sa.BigInteger(), nullable=True))

    op.add_column("message_parts", sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("message_parts", sa.Column("time_updated", sa.BigInteger(), nullable=True))

    # Backfill ordinals before the unique index; works for MySQL and PostgreSQL.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, session_id, message_id FROM message_parts"
            " ORDER BY session_id, message_id, time_created, id"
        )
    ).fetchall()
    current_key = None
    ordinal = 0
    for part_id, session_id, message_id in rows:
        key = (session_id, message_id)
        if key != current_key:
            current_key = key
            ordinal = 0
        bind.execute(
            sa.text(
                "UPDATE message_parts SET ordinal = :ordinal"
                " WHERE session_id = :session_id AND id = :id"
            ),
            {"ordinal": ordinal, "session_id": session_id, "id": part_id},
        )
        ordinal += 1

    op.create_index(
        "uq_parts_message_ordinal",
        "message_parts",
        ["session_id", "message_id", "ordinal"],
        unique=True,
    )
    op.create_index(
        "idx_parts_message_ordinal",
        "message_parts",
        ["message_id", "ordinal"],
        unique=False,
    )

    op.add_column("session_inputs", sa.Column("type", sa.String(16), nullable=False, server_default="user"))
    op.add_column("session_inputs", sa.Column("status", sa.String(16), nullable=False, server_default="pending"))
    op.add_column("session_inputs", sa.Column("time_delivered", sa.BigInteger(), nullable=True))
    bind.execute(
        sa.text(
            "UPDATE session_inputs SET status = 'delivered',"
            " time_delivered = COALESCE(time_delivered, promoted_seq, time_created)"
            " WHERE promoted_seq IS NOT NULL AND status = 'pending'"
        )
    )
    op.create_index(
        "idx_inputs_session_status",
        "session_inputs",
        ["session_id", "status"],
        unique=False,
    )

    op.create_table(
        "session_permissions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(64), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resources", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("save", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("metadata", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("reply", sa.String(16), nullable=True),
        sa.Column("time_created", sa.BigInteger(), nullable=False),
        sa.Column("time_resolved", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "idx_permissions_session_status",
        "session_permissions",
        ["session_id", "status"],
        unique=False,
    )

    op.create_table(
        "session_permission_saved",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("effect", sa.String(16), nullable=False, server_default="allow"),
        sa.Column("time_created", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("user_id", "action", "resource"),
    )
    op.create_index(
        "idx_saved_permissions_user",
        "session_permission_saved",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "session_forms",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(64), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("fields", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("response", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("time_created", sa.BigInteger(), nullable=False),
        sa.Column("time_updated", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "idx_forms_session_state",
        "session_forms",
        ["session_id", "state"],
        unique=False,
    )

    op.create_table(
        "schema_migrations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("applied_at", sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("schema_migrations")
    op.drop_index("idx_forms_session_state", table_name="session_forms")
    op.drop_table("session_forms")
    op.drop_index("idx_saved_permissions_user", table_name="session_permission_saved")
    op.drop_table("session_permission_saved")
    op.drop_index("idx_permissions_session_status", table_name="session_permissions")
    op.drop_table("session_permissions")

    op.drop_index("idx_inputs_session_status", table_name="session_inputs")
    op.drop_column("session_inputs", "time_delivered")
    op.drop_column("session_inputs", "status")
    op.drop_column("session_inputs", "type")

    op.drop_index("idx_parts_message_ordinal", table_name="message_parts")
    op.drop_index("uq_parts_message_ordinal", table_name="message_parts")
    op.drop_column("message_parts", "time_updated")
    op.drop_column("message_parts", "ordinal")

    op.drop_column("session_messages", "time_completed")

    op.drop_column("sessions", "time_viewed")
    op.drop_column("sessions", "time_idle")
    op.drop_column("sessions", "time_busy")
    op.drop_column("sessions", "revert_state")
    op.drop_column("sessions", "metadata")
    op.drop_column("sessions", "outcome")
