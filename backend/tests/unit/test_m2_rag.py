"""L1 单元测试：RAG 管线（agent.md §4.1）。"""

from cognition.rag import RAGPipeline, rag_search


def _fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0, 0.0, 0.0, 0.0]
        for i, ch in enumerate(t):
            v[i % 4] += (ord(ch) % 7) + 1
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        out.append([x / norm for x in v])
    return out


def _pipeline(tmp_path, threshold=0.0):
    return RAGPipeline(
        collection_name="kb_test",
        persist_dir=str(tmp_path),
        embed_fn=_fake_embed,
        score_threshold=threshold,
    )


def test_index_and_retrieve_roundtrip(tmp_path):
    pipe = _pipeline(tmp_path)
    n = pipe.index(
        ["退款政策：7天内可退", "公司地址：北京市朝阳区"],
        metadatas=[{"source": "policy.md"}, {"source": "about.md"}],
    )
    assert n == 2 and pipe.count == 2
    hits = pipe.retrieve("退款政策：7天内可退", top_k=5)
    assert hits and hits[0]["content"] == "退款政策：7天内可退"
    assert hits[0]["meta"]["source"] == "policy.md"


def test_threshold_filters_everything(tmp_path):
    pipe = _pipeline(tmp_path, threshold=0.0)
    pipe.index(["a", "b"], metadatas=[{"source": "s1"}, {"source": "s2"}])
    assert pipe.retrieve("a", top_k=5)
    # 阈值上调至不可能达到 → 全部被过滤
    pipe.score_threshold = 1.01
    assert pipe.retrieve("a", top_k=5) == []


def test_format_context_labels_sources(tmp_path):
    chunks = [
        {"content": "内容一", "meta": {"source": "doc.md", "page": 3}, "score": 0.9},
        {"content": "内容二", "meta": {"filename": "other.txt"}, "score": 0.8},
    ]
    text = RAGPipeline.format_context(chunks)
    assert "[1] 内容一" in text and "doc.md#p3" in text
    assert "[2] 内容二" in text and "other.txt" in text


def test_rag_search_returns_sources(tmp_path):
    pipe = _pipeline(tmp_path)
    pipe.index(["hello world"], metadatas=[{"source": "a.md"}])
    out = rag_search("hello world", pipe, top_k=3)
    assert out["context"]
    assert out["sources"] == ["a.md"]
