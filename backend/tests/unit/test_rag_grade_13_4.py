"""L1 单元测试：§13.4 检索质量门禁 grade_docs + 查询改写 rewrite（agent-2.md §13.4）。"""

from langchain_core.messages import HumanMessage

from graph.nodes.rag import make_grade_node, make_rag_node, make_rewrite_node
from graph.routers import route_after_grade


class _FakeModel:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def invoke(self, prompt):
        self.calls.append(prompt)

        class _Resp:
            content = self.content

        return _Resp()


def _state(**kw):
    base = {"messages": [HumanMessage(content="TS-999 故障码是什么意思")]}
    base.update(kw)
    return base


# ---- grade ----


def test_grade_yes_is_relevant():
    node = make_grade_node(_FakeModel("yes"))
    out = node(_state(rag_context="TS-999 故障码：编码器过热"))
    assert out["rag_relevant"] is True


def test_grade_no_is_irrelevant():
    node = make_grade_node(_FakeModel("no"))
    out = node(_state(rag_context="本季度团建安排"))
    assert out["rag_relevant"] is False


def test_grade_empty_context_passes_without_model_call():
    model = _FakeModel("no")
    node = make_grade_node(model)
    out = node(_state(rag_context=""))
    # 空结果放行，且不消耗模型调用（避免 2 轮无效 grade/rewrite）
    assert out["rag_relevant"] is True
    assert model.calls == []


def test_grade_without_model_passes_through():
    out = make_grade_node(None)(_state(rag_context="任意内容"))
    assert out["rag_relevant"] is True


def test_grade_model_failure_passes_through():
    class _Boom:
        def invoke(self, _prompt):
            raise RuntimeError("network down")

    out = make_grade_node(_Boom())(_state(rag_context="任意内容"))
    assert out["rag_relevant"] is True


# ---- rewrite ----


def test_rewrite_parses_json_and_increments_count():
    model = _FakeModel('{"rewritten_query": "TS-999 故障码 编码器过热", "is_retrieval": true}')
    out = make_rewrite_node(model)(_state())
    assert out["rewritten_query"] == "TS-999 故障码 编码器过热"
    assert out["rewrite_count"] == 1


def test_rewrite_count_accumulates():
    model = _FakeModel("TS-999 故障码")
    out = make_rewrite_node(model)(_state(rewrite_count=1))
    assert out["rewrite_count"] == 2


def test_rewrite_without_model_keeps_original():
    out = make_rewrite_node(None)(_state())
    assert out["rewritten_query"] == "TS-999 故障码是什么意思"
    assert out["rewrite_count"] == 1


def test_rewrite_prompt_embeds_user_query():
    """回归：PromptLoader 自身做变量替换，模板占位符必须真正被填充。"""
    model = _FakeModel('{"rewritten_query": "X"}')
    make_rewrite_node(model)(_state())
    prompt = model.calls[0]
    assert "TS-999 故障码是什么意思" in prompt
    assert "{{user_query}}" not in prompt
    assert "$user_query" not in prompt


def test_rewrite_empty_query_falls_back_to_original():
    """回归：模型返回空 rewritten_query 时不得把整段 JSON 当查询。"""
    model = _FakeModel('{"rewritten_query": "", "is_retrieval": false}')
    out = make_rewrite_node(model)(_state())
    assert out["rewritten_query"] == "TS-999 故障码是什么意思"


# ---- route_after_grade ----


def test_route_generate_when_relevant():
    assert route_after_grade({"rag_relevant": True}) == "generate"


def test_route_rewrite_when_irrelevant_below_limit():
    assert route_after_grade({"rag_relevant": False, "rewrite_count": 0}) == "rewrite"
    assert route_after_grade({"rag_relevant": False, "rewrite_count": 1}) == "rewrite"


def test_route_generate_when_rewrite_limit_reached():
    assert route_after_grade({"rag_relevant": False, "rewrite_count": 2}) == "generate"


# ---- rag 节点优先用改写查询 ----


def test_rag_node_prefers_rewritten_query():
    seen = []

    def retrieve(query, top_k):
        seen.append(query)
        return [{"content": "命中", "score": 0.9}]

    node = make_rag_node(retrieve)
    node(_state(rewritten_query="改写后的查询"))
    assert seen == ["改写后的查询"]


def test_rag_node_falls_back_to_user_message():
    seen = []

    def retrieve(query, top_k):
        seen.append(query)
        return []

    node = make_rag_node(retrieve)
    node(_state(rewritten_query=""))
    assert seen == ["TS-999 故障码是什么意思"]
