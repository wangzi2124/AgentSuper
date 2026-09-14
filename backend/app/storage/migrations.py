"""启动时执行 schema 迁移（Alembic）。

- sqlite：schema 由各子系统连接时幂等建立（metadata 派生 DDL），无需迁移链。
- mysql / postgresql：执行 `alembic upgrade head`，初始迁移 0001 从
  `app.storage.schema.metadata` 全量建表，与 sqlite 的建表路径同源。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"  # backend/alembic
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"  # backend/alembic.ini


def run_migrations() -> bool:
    """执行 schema 迁移。返回是否实际执行过（True=非 sqlite 后端已 upgrade head）。"""
    from app.storage import backends

    if backends.is_sqlite():
        return False
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(_ALEMBIC_INI))
        cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
        cfg.set_main_option("sqlalchemy.url", backends.database_url())
        command.upgrade(cfg, "head")
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("Alembic upgrade head failed: %s", e, exc_info=True)
        raise