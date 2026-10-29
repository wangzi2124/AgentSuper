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

def test_recall_ranks_by_query_when_store_has_no_index():
    from types import SimpleNamespace

    from graph.nodes.memory import make_memory_recall_node

    class _Store:
        def search(self, namespace, query=None, limit=10):
            return [
                SimpleNamespace(value={"type": "fact", "content": "香蕉很甜"}),
                SimpleNamespace(value={"type": "fact", "content": "苹果是红色的"}),
            ]

    node = make_memory_recall_node(_Store())
    out = node(
        {
            "user_id": "u1",
            "messages": [SimpleNamespace(type="human", content="苹果")],
        }
    )
    assert "苹果" in out["memory_context"]
    assert out["memory_context"].lstrip().startswith("- [fact] 苹果")
