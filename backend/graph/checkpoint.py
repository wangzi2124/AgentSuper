"""检查点工厂（M4）：MemorySaver 默认，Postgres 可选。

未安装 psycopg 时自动降级为 MemorySaver。
"""

from __future__ import annotations

import os
from typing import Any

from langgraph.checkpoint.memory import MemorySaver


def get_checkpointer(configured: Any = None) -> Any:
    if configured is not None:
        return configured
    url = os.environ.get("POSTGRES_CHECKPOINT_URL") or os.environ.get("DATABASE_URL")
    if url and "postgres" in url:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore
            return PostgresSaver.from_conn_string(url)
        except Exception:
            return MemorySaver()
    return MemorySaver()
