"""Alembic 迁移环境（多后端）。

连接串来源优先级：
1. alembic.config 的 sqlalchemy.url（启动时由 app.storage.migrations.run_migrations() 注入）
2. app.config.settings → app.storage.backends.database_url()

相关：backend/alembic/versions/0001_initial.py（初始迁移 = metadata.create_all）。
"""
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# backend/ 加入 sys.path，保证 `app` 包可导入（alembic 命令行在 backend/ 下执行时也成立）
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.storage import schema as storage_schema  # noqa: E402

config = context.config

# 注意：这里刻意不调用 logging.config.fileConfig —— 迁移的日志直接走 Python logging
# 根 handler（掷入 stderr）；更重要的是避免 fileConfig 重置（disable_existing_loggers）
# 干扰 pytest 的 caplog 捕获同一次测试会话中的其他用例日志。

target_metadata = storage_schema.metadata


def _url() -> str:
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    from app.storage.backends import database_url

    return database_url()


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        {"sqlalchemy.url": _url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()