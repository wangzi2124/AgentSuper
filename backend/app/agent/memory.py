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
import json
import time
import time as tmod
import logging
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field

from app.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 300  # 默认 5 分钟

# 持久化去抖间隔（秒）：多个 set 在短时间内触发只落盘一次，减少同步全量写 IO
_PERSIST_DEBOUNCE = 1.0


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

    def __init__(self, default_ttl: float = _DEFAULT_TTL, persist_path: Optional[str] = None):
        self._store: dict[str, MemoryEntry] = {}
        self._default_ttl = default_ttl
        self._lock = asyncio.Lock()
        # 持久化：默认使用 settings.memory_persist_path（空串/None 表示不落盘）
        self._persist_path = persist_path if persist_path is not None else settings.memory_persist_path
        # 异步去抖落盘状态
        self._persist_debounce_lock = asyncio.Lock()
        self._last_persist_ts = 0.0
        self._load()

    def _ns_key(self, key: str, namespace: str = "") -> str:
        return f"{namespace}:{key}" if namespace else key

    def _load(self) -> None:
        """启动时从磁盘加载未过期记忆。"""
        if not self._persist_path:
            return
        try:
            p = Path(self._persist_path)
            if not p.exists():
                return
            data = json.loads(p.read_text(encoding="utf-8"))
            now = time.time()
            for item in data.get("entries", []) if isinstance(data, dict) else []:
                try:
                    created = float(item.get("created_at", now))
                    ttl = float(item.get("ttl", self._default_ttl))
                    if now - created > ttl:
                        continue
                    self._store[self._ns_key(item["key"], item.get("namespace", ""))] = MemoryEntry(
                        key=item["key"],
                        value=item.get("value"),
                        ttl=ttl,
                        tags=item.get("tags") or [],
                        namespace=item.get("namespace", ""),
                        created_at=created,
                    )
                except Exception:  # noqa: BLE001
                    continue
            logger.info("MemoryManager loaded %d entries from %s", len(self._store), p)
        except Exception as e:  # noqa: BLE001
            logger.warning("MemoryManager failed to load persistence: %s", e)

    def _persist(self) -> None:
        """把未过期记忆落盘（值不可序列化时降级为 str）。

        同步全量写（对齐原实现）：保证 set/delete 返回后数据已落盘，重启不丢。
        调用方通常处于 async 锁内，且本文件很小（记忆条目级），开销可接受。
        若要进一步降低写频可改用 _persist_async_debounced（见下）。
        """
        if not self._persist_path:
            return
        try:
            self._persist_sync()
        except Exception as e:  # noqa: BLE001
            logger.warning("MemoryManager persist failed: %s", e)

    def _persist_sync(self) -> None:
        now = time.time()
        entries = []
        for e in self._store.values():
            if e.expired:
                continue
            entries.append({
                "key": e.key,
                "value": e.value,
                "ttl": e.ttl,
                "tags": e.tags,
                "namespace": e.namespace,
                "created_at": e.created_at,
            })
        p = Path(self._persist_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    async def _persist_async_debounced(self) -> None:
        """去抖异步落盘：短时间多次 set 只写一次磁盘。

        对齐 opencode storage 的异步语义——避免在事件循环内做同步全量写 IO，
        多 Agent 并发写共享记忆时不会阻塞各自的事件循环。
        注意：调用后数据可能尚未写盘（去抖窗口内），进程异常退出会丢最近 1s 数据。
        """
        if not self._persist_path:
            return
        async with self._persist_debounce_lock:
            now = tmod.monotonic()
            if now - self._last_persist_ts < _PERSIST_DEBOUNCE:
                return
            self._last_persist_ts = now
            try:
                await asyncio.to_thread(self._persist_sync)
            except Exception as e:  # noqa: BLE001
                logger.warning("MemoryManager async persist failed: %s", e)

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
            self._persist()

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
            if self._store.pop(full_key, None) is not None:
                self._persist()

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
                self._persist()

    async def clear(self):
        """清空所有记忆。"""
        async with self._lock:
            self._store.clear()
            self._persist()

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
