"""工作区读写锁：写者优先，读不能饿死写。"""

from __future__ import annotations

import asyncio

from graph.nodes.base import _AsyncRWLock


async def test_writer_gets_priority_over_new_readers():
    lock = _AsyncRWLock()
    await lock.acquire_read()

    order: list[str] = []

    async def writer() -> None:
        await lock.acquire_write()
        order.append("w")
        await lock.release_write()

    async def reader() -> None:
        await lock.acquire_read()
        order.append("r")
        await lock.release_read()

    write_task = asyncio.create_task(writer())
    await asyncio.sleep(0)
    assert lock._waiting_writers == 1

    read_task = asyncio.create_task(reader())
    await asyncio.sleep(0)
    assert order == []

    await lock.release_read()
    await asyncio.gather(write_task, read_task)

    assert order == ["w", "r"]
