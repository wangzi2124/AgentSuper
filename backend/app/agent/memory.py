"""Agent 间共享记忆管理器。

允许不同 Agent 之间共享上下文信息，避免重复检索。
每个条目包含：
  - key: 记忆键（如 "last_search_query"）
  - value: 记忆值
  - ttl: 过期时间（秒）
  - tags: 标签列表，用于筛选和清理
  - namespace: 命名空间（如 conversation_id），用于隔离不同会话的记忆

🔒 Session 隔离: 所有 set/get/delete/get_by_tag 都支持 namespace 参数，
  不同 conversation 的记忆不会互相干扰。
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
    namespace: str = ""
    created_at: float = field(default_factory=time.time)

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > self.ttl


class MemoryManager:
    """共享记忆管理器。

    用法:
        mm = MemoryManager()
        # 全局记忆（所有 conversation 共享）
        mm.set("python_version", "3.11", tags=["code", "env"])
        # 按 conversation 隔离的记忆
        mm.set("last_search", "result", namespace="conv_xxx", tags=["web_search"])
        val = mm.get("last_search", namespace="conv_xxx")
        all_code = mm.get_by_tag("code")
        mm.cleanup()
    """

    def __init__(self, default_ttl: float = _DEFAULT_TTL):
        self._store: dict[str, MemoryEntry] = {}
        self._default_ttl = default_ttl
        self._lock = asyncio.Lock()

    def _ns_key(self, key: str, namespace: str = "") -> str:
        return f"{namespace}:{key}" if namespace else key

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[float] = None,
        tags: Optional[list[str]] = None,
        namespace: str = "",
    ):
        """存储一条记忆。

        Args:
            key: 记忆键
            value: 记忆值
            ttl: 过期时间（秒）
            tags: 标签列表
            namespace: 命名空间（如 conversation_id），不同命名空间的 key 不冲突
        """
        async with self._lock:
            full_key = self._ns_key(key, namespace)
            self._store[full_key] = MemoryEntry(
                key=key,
                value=value,
                ttl=ttl or self._default_ttl,
                tags=tags or [],
                namespace=namespace,
            )

    async def get(self, key: str, default: Any = None, namespace: str = "") -> Any:
        """读取一条记忆。已过期或不存在时返回 default。

        Args:
            key: 记忆键
            default: 默认值
            namespace: 命名空间，必须和 set 时一致才能读到
        """
        async with self._lock:
            full_key = self._ns_key(key, namespace)
            entry = self._store.get(full_key)
            if entry is None or entry.expired:
                if entry and entry.expired:
                    del self._store[full_key]
                return default
            return entry.value

    async def delete(self, key: str, namespace: str = ""):
        """删除一条记忆。"""
        async with self._lock:
            full_key = self._ns_key(key, namespace)
            self._store.pop(full_key, None)

    async def get_by_tag(self, tag: str, namespace: str = "") -> dict[str, Any]:
        """获取所有带指定标签的未过期记忆。

        Args:
            tag: 标签名
            namespace: 如果提供，只返回该命名空间下的匹配条目
        """
        async with self._lock:
            now = time.time()
            result = {}
            expired_keys = []
            for full_key, entry in self._store.items():
                if entry.expired:
                    expired_keys.append(full_key)
                    continue
                if tag not in entry.tags:
                    continue
                if namespace and entry.namespace != namespace:
                    continue
                result[entry.key] = entry.value
            for k in expired_keys:
                del self._store[k]
            return result

    async def cleanup(self, namespace: str = ""):
        """清理过期条目。如果指定 namespace，只清理该命名空间。"""
        async with self._lock:
            now = time.time()
            expired = []
            for full_key, entry in self._store.items():
                if entry.expired:
                    if namespace and entry.namespace != namespace:
                        continue
                    expired.append(full_key)
            for k in expired:
                del self._store[k]
            if expired:
                logger.debug("MemoryManager cleaned %d expired entries", len(expired))

    async def clear_namespace(self, namespace: str):
        """清空指定命名空间下的所有记忆（用于对话结束时清理）。"""
        async with self._lock:
            keys = [
                k for k, e in self._store.items()
                if e.namespace == namespace
            ]
            for k in keys:
                del self._store[k]
            if keys:
                logger.debug("MemoryManager cleared namespace '%s' (%d entries)", namespace, len(keys))

    async def clear(self):
        """清空所有记忆。"""
        async with self._lock:
            self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def active_size(self) -> int:
        now = time.time()
        return sum(1 for e in self._store.values() if not e.expired)

    async def to_dict(self, include_tags: Optional[list[str]] = None, namespace: str = "") -> dict[str, Any]:
        """导出记忆为字典。可按标签和命名空间筛选。"""
        result = {}
        async with self._lock:
            now = time.time()
            for full_key, entry in self._store.items():
                if entry.expired:
                    continue
                if namespace and entry.namespace != namespace:
                    continue
                if include_tags and not any(t in entry.tags for t in include_tags):
                    continue
                result[entry.key] = entry.value
        return result
