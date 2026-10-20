"""L1 单元测试：§13.6 子块命中 → 父块展开（VectorStore.get_parents）。"""

from cognition.vector_store import VectorStore


def _store(tmp_path):
    return VectorStore(str(tmp_path))


def test_get_parents_returns_parent_text(tmp_path):
    vs = _store(tmp_path)
    vs.add(
        ["退货政策 父块正文", "子块内容 A", "子块内容 B"],
        [
            {"document_id": "d1", "parent_id": "d1#s1", "is_parent": True},
            {"document_id": "d1", "parent_id": "d1#s1", "is_parent": False},
            {"document_id": "d1", "parent_id": "d1#s1", "is_parent": False},
        ],
        [[1.0, 0.0], [0.9, 0.1], [0.8, 0.2]],
    )
    parents = vs.get_parents(["d1#s1"])
    assert parents == {"d1#s1": "退货政策 父块正文"}


def test_get_parents_empty_input(tmp_path):
    assert _store(tmp_path).get_parents([]) == {}


def test_get_parents_missing_id_is_ignored(tmp_path):
    vs = _store(tmp_path)
    vs.add(
        ["父块"],
        [{"document_id": "d1", "parent_id": "d1#s1", "is_parent": True}],
        [[1.0, 0.0]],
    )
    assert vs.get_parents(["d1#unknown"]) == {}
