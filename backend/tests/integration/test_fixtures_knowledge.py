"""L2 集成测试：固定夹具知识检索（agent.md §9 tests/fixtures/）。

从 `tests/fixtures/sample_knowledge.txt` 读取固定文本，走 RAG 入库 + 检索闭环，
验证夹具数据可稳定复现（假嵌入 → 无需外部模型）。
"""

from __future__ import annotations

from pathlib import Path

from cognition.rag import RAGPipeline

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_knowledge.txt"


def _fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0] * 8
        for i, ch in enumerate(t):
            v[i % 8] += (ord(ch) % 11) + 1
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        out.append([x / norm for x in v])
    return out


def test_fixture_exists_and_nonempty():
    assert FIXTURE.exists(), f"缺少夹具文件: {FIXTURE}"
    assert FIXTURE.read_text(encoding="utf-8").strip()


def test_fixture_index_and_retrieve(tmp_path):
    text = FIXTURE.read_text(encoding="utf-8")
    chunks = [line.strip() for line in text.splitlines() if len(line.strip()) > 8]
    assert chunks

    pipe = RAGPipeline(
        collection_name="fixture_kb",
        persist_dir=str(tmp_path),
        embed_fn=_fake_embed,
        score_threshold=0.0,
    )
    pipe.index(chunks, metadatas=[{"source": "sample_knowledge.txt"} for _ in chunks])
    assert pipe.count == len(chunks)

    hits = pipe.retrieve("VirtualFileSystem 沙箱根", top_k=3)
    assert hits
    assert all(h["meta"]["source"] == "sample_knowledge.txt" for h in hits)
