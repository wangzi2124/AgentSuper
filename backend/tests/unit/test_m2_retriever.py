"""L1 单元测试：混合检索（agent.md §4.1）。"""

from cognition.retriever import BM25Retriever, HybridRetriever


class _BaseRetriever:
    def __init__(self, items):
        self.items = items

    def invoke(self, query, k):
        return self.items


def test_normalize_tuple_form():
    base = _BaseRetriever([
        ({"text": "退款 7 天", "metadata": {"source": "p.md"}}, 0.8),
    ])
    hits = HybridRetriever(base).retrieve("退款", top_k=5)
    assert hits[0]["content"] == "退款 7 天"
    assert hits[0]["meta"]["source"] == "p.md"
    assert hits[0]["score"] == 0.8


def test_threshold_filters():
    base = _BaseRetriever([
        {"content": "高", "score": 0.9},
        {"content": "低", "score": 0.1},
    ])
    hits = HybridRetriever(base, score_threshold=0.5).retrieve("q", top_k=5)
    assert [h["content"] for h in hits] == ["高"]


def test_fallback_when_base_empty():
    fb = BM25Retriever(["mysql 连接池配置", "redis 缓存策略"])
    r = HybridRetriever(base=None, fallback=fb)
    hits = r.retrieve("mysql 连接池", top_k=2)
    assert hits and "mysql" in hits[0]["content"]


def test_bm25_ranking_prefers_match():
    r = BM25Retriever(["apple banana", "apple apple apple", "cherry"])
    hits = r.retrieve("apple", top_k=3)
    assert hits[0]["content"].startswith("apple")


def test_call_alias():
    r = HybridRetriever(_BaseRetriever([{"content": "x", "score": 1.0}]))
    assert r("q", 1)[0]["content"] == "x"
