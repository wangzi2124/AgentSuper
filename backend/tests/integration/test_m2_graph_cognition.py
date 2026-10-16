"""L2 集成测试：cognition 接入图节点（agent.md §3.2 / §4.1 / §4.3）。"""

import pytest
from langchain_core.messages import HumanMessage

from cognition.compressor import compress_context
from execution.filesystem import VirtualFileSystem
from graph.main_graph import build_main_graph, run_agent
from graph.nodes.compress import make_compress_node
from models.router import ChatResult


class ScriptedModel:
    def __init__(self):
        self.tier = "fake"

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return ChatResult(content="最终答案", finish_reason="stop")


class RecordingReranker:
    def __init__(self):
        self.called = 0

    def rerank(self, query, documents, top_k=3):
        self.called += 1
        return [dict(d, rerank_score=d.get("score", 0.0)) for d in documents[::-1]][:top_k]


@pytest.mark.asyncio
async def test_rag_node_uses_cognition_retriever_and_reranker(tmp_path):
    def retrieve(query, top_k):
        return [
            {"content": "低相关", "meta": {"source": "b.md"}, "score": 0.4},
            {"content": "高相关", "meta": {"source": "a.md"}, "score": 0.9},
        ]

    rr = RecordingReranker()
    graph = build_main_graph(
        model=ScriptedModel(),
        vfs=VirtualFileSystem(root=str(tmp_path)),
        retrieve=retrieve,
        reranker=rr,
    )
    out = await run_agent("[NEED_RAG] 查询", graph=graph, thread_id="m2-rag")
    assert rr.called == 1
    assert out["rag_context"]
    assert "a.md" in out["rag_context"] and "b.md" in out["rag_context"]


@pytest.mark.asyncio
async def test_compress_node_uses_injected_compressor():
    class FakeModel:
        async def ainvoke(self, messages, **kwargs):
            return ChatResult(content="压缩后的结构化摘要")

    node = make_compress_node(
        model=FakeModel(),
        compressor=lambda m, e, mdl: compress_context(
            m, existing_summary=e, threshold=5, keep_tokens=1, model=mdl
        ),
    )
    state = {"messages": [HumanMessage(content="很长的一段内容" * 30) for _ in range(6)]}
    payload = await node(state)
    assert payload["token_usage"]["compressed"] is True
    assert payload["context_summary"] == "压缩后的结构化摘要"
    assert len(payload["messages"]) <= 3  # 摘要 system + 最近 1 条


@pytest.mark.asyncio
async def test_compress_node_noop_below_threshold():
    node = make_compress_node(
        compressor=lambda m, e, mdl: compress_context(m, existing_summary=e, threshold=10**9, model=mdl)
    )
    payload = await node({"messages": [HumanMessage(content="短")]})
    assert payload["token_usage"]["compressed"] is False
