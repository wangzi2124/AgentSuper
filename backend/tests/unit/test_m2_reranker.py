"""L1 单元测试：重排序（agent.md §4.1/§4.2）。"""

from cognition.reranker import Reranker


def test_disabled_reranker_is_identity():
    r = Reranker(enabled=False)
    docs = [{"content": "a", "score": 0.5}, {"content": "b", "score": 0.4}]
    out = r.rerank("q", docs, top_k=2)
    assert [d["content"] for d in out] == ["a", "b"]
    assert out[0]["rerank_score"] == 0.5
    assert r.available is False


def test_reranker_truncates_to_top_k():
    r = Reranker(enabled=False)
    docs = [{"content": str(i), "score": float(i)} for i in range(5)]
    out = r.rerank("q", docs, top_k=2)
    assert len(out) == 2


def test_empty_documents():
    assert Reranker(enabled=False).rerank("q", [], top_k=3) == []
