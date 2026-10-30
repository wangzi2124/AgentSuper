"""检查点工厂：AsyncSqliteSaver 持久化默认（checkpoints.db），内存/Postgres 可选。

历史问题：本文件（及 `build_main_graph` 默认的 MemorySaver）曾是零调用方的死代码，
图 checkpointer 实为进程内 MemorySaver → **后端一重启，会话消息/对话上下文全丢**
（`GET /sessions/{id}/messages` 空、追问失忆、fork/revert 建立在不存在的状态上）。

现在：
- 默认 `AsyncSqliteSaver`：会话消息与图状态跨重启存活——历史回放、追问上下文、
  revert/fork 都读写统一数据根下的 `checkpoints.db`（路径由 `CHECKPOINT_DB_PATH` 覆盖）。
- `CHECKPOINT_PERSIST=false|0|off|no` → 回落 MemorySaver（一次性调试）。
- `POSTGRES_CHECKPOINT_URL` / `DATABASE_URL`（postgres://）→ PostgresSaver（需 psycopg，
  未安装或初始化失败时自动回落 SQLite）。
- 传入 `configured` → 原样返回（测试注入 MemorySaver 用）。

用法（app/main.py lifespan）::

    checkpointer = await get_checkpointer()
    app.state.platform = build_context(..., checkpointer=checkpointer)
    ...
    await aclose_checkpointer()   # 关闭时释放连接
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from config import settings

logger = logging.getLogger(__name__)

_instance: Any = None  # 当前持久化 saver 单例（进程内共享）
_cm: Any = None  # 创建它的 async context manager（关闭时 __aexit__ 释放连接）


def checkpoint_db_path() -> Path:
    """SQLite 检查点文件路径（settings.checkpoint_db_path，缺省 `checkpoints.db`）。"""
    from storage.paths import data_path

    return data_path(settings.checkpoint_db_path, "checkpoints.db")


def _persist_disabled() -> bool:
    return not settings.checkpoint_persist


async def get_checkpointer(configured: Any = None) -> Any:
    """返回图 checkpointer（幂等；默认持久化 SQLite）。

    - `configured` 非空 → 原样返回（调用方自定，测试常用 `MemorySaver()`）。
    - 否则按环境返回单例：Postgres（若配置且可用）> SQLite > MemorySaver。
    """
    global _instance, _cm
    if configured is not None:
        return configured
    if _persist_disabled():
        return MemorySaver()

    url = settings.postgres_checkpoint_url or settings.database_url
    if url and "postgres" in url:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore

            cm = PostgresSaver.from_conn_string(url)
            saver = await cm.__aenter__()
            await saver.setup()
            _cm, _instance = cm, saver
            logger.info("checkpointer: postgres (%s)", url.split("@")[-1])
            return saver
        except Exception:  # noqa: BLE001 - psycopg 缺失等 → 回落 SQLite
            logger.warning("Postgres checkpointer 初始化失败，回落 SQLite", exc_info=True)
            _cm = None

    if _instance is None:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        path = checkpoint_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        cm = AsyncSqliteSaver.from_conn_string(str(path))
        saver = await cm.__aenter__()
        await saver.setup()  # 幂等建表（WAL + checkpoints/writes）
        _cm, _instance = cm, saver
        logger.info("checkpointer: sqlite %s", path)
    return _instance


async def aclose_checkpointer() -> None:
    """关闭持久化 saver 并释放连接（应用 shutdown 时调用；幂等）。"""
    global _instance, _cm
    cm, _cm = _cm, None
    _instance = None
    if cm is None:
        return
    try:
        await cm.__aexit__(None, None, None)
    except Exception:  # noqa: BLE001 - 关闭失败不阻塞退出
        logger.warning("checkpointer 关闭失败", exc_info=True)
