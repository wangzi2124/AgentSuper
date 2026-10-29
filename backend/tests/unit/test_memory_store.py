"""持久化长期记忆 Store：跨实例（模拟重启）仍可召回。"""

from __future__ import annotations

from config import settings
from app.memory_store import get_memory_store, reset_memory_store_for_tests

NS = ("user-1", "memories")


def test_sqlite_store_persists_across_instances(tmp_path, monkeypatch):
    db = tmp_path / "memory.db"
    monkeypatch.setattr(settings, "memory_persist_path", str(db))
    reset_memory_store_for_tests()
    try:
        first = get_memory_store()
        first.put(NS, "k1", {"content": "ORION-42", "type": "fact"})
        reset_memory_store_for_tests()

        second = get_memory_store()
        item = second.get(NS, "k1")
        assert item is not None
        assert item.value["content"] == "ORION-42"

        hits = second.search(NS, query="ORION-42", limit=3)
        assert any(getattr(h, "value", {}).get("content") == "ORION-42" for h in hits)
    finally:
        reset_memory_store_for_tests()
