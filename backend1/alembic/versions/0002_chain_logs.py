"""add chain_logs table (full-chain tracing)

新增「全链路日志」表 `chain_logs`（增量迁移，不改动 0001 已建的其他表）。
该表在 `app.storage.schema` 中与其它子系统表同源声明（CHAIN_LOG_TABLES），
本迁移只补齐链式日志子系统的表与索引，供 mysql / postgresql 后端使用
（sqlite 后端连接时由 derive_sqlite_ddl 幂等建表，无需迁移）。

Revision ID: 0002_chain_logs
Revises: 0001_initial
Create Date: 2026-09-27
"""
from alembic import op

revision = "0002_chain_logs"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.storage import schema as storage_schema

    # create_all(bind=..., tables=[...]) 只建本次新增的表，不触碰已有表
    storage_schema.metadata.create_all(
        bind=op.get_bind(),
        tables=[storage_schema.metadata.tables[t] for t in storage_schema.CHAIN_LOG_TABLES],
    )


def downgrade() -> None:
    from app.storage import schema as storage_schema

    storage_schema.metadata.drop_all(
        bind=op.get_bind(),
        tables=[storage_schema.metadata.tables[t] for t in storage_schema.CHAIN_LOG_TABLES],
    )
