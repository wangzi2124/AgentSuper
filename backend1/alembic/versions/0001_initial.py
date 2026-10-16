"""initial schema (all subsystems)

四个 SQLite 子系统的表统一在此迁移（mysql/postgresql 共享同一数据库）。
初始迁移直接复用 `app.storage.schema.metadata`（单一声明来源），
后续 schema 演进在此基础上追加 revisions。

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-14
"""
from alembic import op

# 避免 alembic 扫描 versions/ 时 import app（环境不完整）——迁移执行时通过
# env.py 已把 backend/ 加入 sys.path，import 时再次确认路径。
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.storage import schema as storage_schema

    storage_schema.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from app.storage import schema as storage_schema

    storage_schema.metadata.drop_all(bind=op.get_bind())