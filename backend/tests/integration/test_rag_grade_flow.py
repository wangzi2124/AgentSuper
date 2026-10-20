"""L2 集成测试：§13.4 grade_docs / rewrite 在图中的真实流转（agent-2.md §13.4）。

用脚本化 FakeModel + 计数检索器验证：
- grade 判 yes → 只检索 1 次；
- grade 恒判 no → rewrite→rag 重试 2 次后放行（共 3 次检索），不死循环。
"""

import pytest

from execution.filesystem import VirtualFileSystem
from graph.main_graph import build_main_graph, run_agent
from models.router import ChatResult


class ScriptedModel:
    def __init__(self, script=None):
        self.tier = "fake"
        self._script = list(script or [])

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        if self._script:
            return self._script.pop(0)
        return ChatResult(content="[done]", finish_reason="stop")


class _GradeModel:
    """同步 grade/rewrite 假模型：`answer` 固定返回内容，记录调用次数。"""

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def invoke(self, _prompt):
        self.calls += 1

        class _Resp:
            content = self.answer

        return _Resp()


def _graph(tmp_path, grade_model, counter):
    def retrieve(query, top_k):
        counter.append(query)
        return [{"content": "TS-999 故障码：编码器过热", "score": 0.9}]

    return build_main_graph(
        model=ScriptedModel([ChatResult(content="完成", finish_reason="stop")]),
        vfs=VirtualFileSystem(root=str(tmp_path)),
        retrieve=retrieve,
        grade_model=grade_model,
        rewrite_model=_GradeModel("TS-999 故障码"),
    )


@pytest.mark.asyncio
async def test_relevant_retrieves_once(tmp_path):
    calls = []
    graph = _graph(tmp_path, _GradeModel("yes"), calls)
    out = await run_agent(
        "TS-999 故障码是什么意思", graph=graph, thread_id="g1", use_vector_db=True
    )
    assert len(calls) == 1
    assert out["rag_relevant"] is True
    assert out["rewrite_count"] == 0


@pytest.mark.asyncio
async def test_irrelevant_rewrites_twice_then_passes(tmp_path):
    calls = []
    graph = _graph(tmp_path, _GradeModel("no"), calls)
    out = await run_agent(
        "TS-999 故障码是什么意思", graph=graph, thread_id="g2", use_vector_db=True
    )
    # 首次 + 2 次改写重试 = 3 次检索，随后强制放行（§13.4.2 rewrite_count >= 2）
    assert len(calls) == 3
    assert out["rewrite_count"] == 2
    assert out["rewritten_query"] == "TS-999 故障码"
    assert out["final_output"]
