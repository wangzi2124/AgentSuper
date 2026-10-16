"""L1 单元测试：长期记忆（agent.md §4.4）。"""

from cognition.memory import LongTermMemory, MemoryManager


def test_remember_recall_forget():
    mem = LongTermMemory(namespace=("memories",))
    mem.remember("user_name", {"content": "张三"})
    assert mem.recall("user_name")["content"] == "张三"
    mem.forget("user_name")
    assert mem.recall("user_name") is None


def test_namespace_isolation():
    mem = LongTermMemory()
    mem.remember("k", {"content": "A"}, namespace="s1")
    mem.remember("k", {"content": "B"}, namespace="s2")
    assert mem.recall("k", namespace="s1")["content"] == "A"
    assert mem.recall("k", namespace="s2")["content"] == "B"


def test_search_and_list():
    mem = LongTermMemory()
    mem.remember("a", {"content": "苹果"})
    mem.remember("b", {"content": "香蕉"})
    entries = mem.list()
    assert {e["key"] for e in entries} == {"a", "b"}


def test_format_entries_block():
    text = LongTermMemory.format_entries(
        [{"key": "a", "value": {"content": "苹果"}}, {"key": "b", "value": "香蕉"}]
    )
    assert text.startswith("<memory>")
    assert "- a: 苹果" in text and "- b: 香蕉" in text


def test_format_empty_returns_blank():
    assert LongTermMemory.format_entries([]) == ""


def test_memory_manager_reexported():
    assert MemoryManager is not None
