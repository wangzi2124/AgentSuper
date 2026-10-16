"""L1 单元测试：路由逻辑（agent.md §10.2 / §3.3）。"""

from langchain_core.messages import HumanMessage

from graph.routers import MAX_ITERATIONS, route_after_supervisor


def test_iteration_hard_stop():
    assert route_after_supervisor({"iteration_count": MAX_ITERATIONS, "messages": []}) == "synthesize"


def test_final_output_ends():
    assert route_after_supervisor({"final_output": "done", "messages": []}) == "end"


def test_compress_on_threshold():
    state = {"token_usage": {"total_tokens": 90_000}, "messages": []}
    assert route_after_supervisor(state) == "compress"


def test_needs_rag():
    state = {"messages": [HumanMessage(content="[NEED_RAG] 查资料")]}
    assert route_after_supervisor(state) == "rag"


def test_needs_memory():
    state = {"messages": [HumanMessage(content="[NEED_MEMORY] 我是谁")]}
    assert route_after_supervisor(state) == "recall"


def test_defaults_to_plan_when_no_plan():
    state = {"messages": [HumanMessage(content="你好")]}
    assert route_after_supervisor(state) == "plan"


def test_pending_plan_executes():
    state = {
        "messages": [HumanMessage(content="x")],
        "task_plan": [{"id": "t1", "status": "pending"}],
    }
    assert route_after_supervisor(state) == "execute"


def test_done_plan_with_results_synthesizes():
    state = {
        "messages": [HumanMessage(content="x")],
        "task_plan": [{"id": "t1", "status": "done"}],
        "sub_results": {"t1": "ok"},
    }
    assert route_after_supervisor(state) == "synthesize"
