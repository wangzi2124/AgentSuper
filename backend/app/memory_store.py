"""持久化 LangGraph Store（长期记忆）。

`memory_persist_path` 指向 SQLite 文件；图节点的 memory_recall / memory_store
通过 BaseStore 读写，重启后仍可召回。默认 `data/agent_memory.db`。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_store: Optional[Any] = None
_conn: Optional[sqlite3.Connection] = None


def _resolve_path() -> Path:
    raw = (settings.memory_persist_path or "data/agent_memory.db").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    return path


def get_memory_store() -> Any:
    """返回进程级持久化 Store；初始化失败时抛异常，由调用方回退。"""
    global _store, _conn
    if _store is not None:
        return _store
    with _lock:
        if _store is not None:
            return _store
        from langgraph.store.sqlite import SqliteStore

        path = _resolve_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        store = SqliteStore(conn)
        store.setup()
        _conn, _store = conn, store
        logger.info("memory store: sqlite (%s)", path)
        return store


def close_memory_store() -> None:
    global _store, _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001
                pass
        _store = None
        _conn = None


def reset_memory_store_for_tests() -> None:
    """测试用：关闭单例并释放连接（不清空 SQLite 文件），便于换路径重建。"""
    close_memory_store()


__all__ = ["get_memory_store", "close_memory_store", "reset_memory_store_for_tests"]
