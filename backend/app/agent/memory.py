"""Agent 间共享记忆管理器。

允许不同 Agent 之间共享上下文信息，避免重复检索。
每个条目包含：
  - key: 记忆键（如 "last_search_query"）
  - value: 记忆值
  - ttl: 过期时间（秒）
  - tags: 标签列表，用于筛选和清理
"""

import asyncio
import time
import logging
from typing import Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 300  # 默认 5 分钟


@dataclass
class MemoryEntry:
    key: str
    value: Any
    ttl: float = _DEFAULT_TTL
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > self.ttl


class MemoryManager:
    """共享记忆管理器。

    用法:
        mm = MemoryManager()
        mm.set("python_version", "3.11", tags=["code", "env"])
        val = mm.get("python_version")
        all_code = mm.get_by_tag("code")
        mm.cleanup()  # 清理过期条目
    """

    def __init__(self, default_ttl: float = _DEFAULT_TTL):
        self._store: dict[str, MemoryEntry] = {}
        self._default_ttl = default_ttl
        self._lock = asyncio.Lock()
        self._cleanup_interval = 60  # 每 60 秒自动清理

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[float] = None,
        tags: Optional[list[str]] = None,
    ):
        """存储一条记忆。"""
        async with self._lock:
            self._store[key] = MemoryEntry(
                key=key,
                value=value,
                ttl=ttl or self._default_ttl,
                tags=tags or [],
            )

    async def get(self, key: str, default: Any = None) -> Any:
        """读取一条记忆。如果已过期或不存在返回 default。"""
        async with self._lock:
            entry = self._store.get(key)
            if entry is None or entry.expired:
                if entry and entry.expired:
                    del self._store[key]
                return default
            return entry.value

    async def delete(self, key: str):
        """删除一条记忆。"""
        async with self._lock:
            self._store.pop(key, None)

    async def get_by_tag(self, tag: str) -> dict[str, Any]:
        """获取所有带指定标签的未过期记忆。"""
        async with self._lock:
            now = time.time()
            result = {}
            expired_keys = []
            for key, entry in self._store.items():
                if entry.expired:
                    expired_keys.append(key)
                    continue
                if tag in entry.tags:
                    result[key] = entry.value
            for k in expired_keys:
                del self._store[k]
            return result

    async def cleanup(self):
        """清理所有过期记忆。"""
        async with self._lock:
            now = time.time()
            expired = [k for k, v in self._store.items() if v.expired]
            for k in expired:
                del self._store[k]
            if expired:
                logger.debug("MemoryManager cleaned %d expired entries", len(expired))

    async def clear(self):
        """清空所有记忆。"""
        async with self._lock:
            self._store.clear()

    @property
    def size(self) -> int:
        """当前条目数（含过期）。"""
        return len(self._store)

    @property
    def active_size(self) -> int:
        """未过期条目数。"""
        now = time.time()
        return sum(1 for e in self._store.values() if not e.expired)

    async def to_dict(self, include_tags: Optional[list[str]] = None) -> dict[str, Any]:
        """导出记忆为字典（用于序列化/调试）。"""
        result = {}
        async with self._lock:
            now = time.time()
            for key, entry in self._store.items():
                if entry.expired:
                    continue
                if include_tags and not any(t in entry.tags for t in include_tags):
                    continue
                result[key] = entry.value
        return result
