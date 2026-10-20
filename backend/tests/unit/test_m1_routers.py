"""L1 单元测试：路由逻辑（agent.md §10.2 / §3.3）。"""

from langchain_core.messages import HumanMessage

from graph.routers import MAX_ITERATIONS, route_after_supervisor


def test_iteration_hard_stop():
    assert route_after_supervisor({"iteration_count": MAX_ITERATIONS, "messages": []}) == "synthesize"


def test_final_output_ends():
    assert route_after_supervisor({"final_output": "done", "messages": []}) == "end"


def test_compress_on_threshold():
    # 压缩触发依据「当前上下文 token 估算」（§4.3），而非用量累加键；且消息数须
    # 超过压缩节点的 KEEP_RECENT，否则触发也缩不了身。
    big = "x" * 60_000  # ≈15k token/条
    messages = [HumanMessage(content=big) for _ in range(7)]
    assert route_after_supervisor({"messages": messages}) == "compress"


def test_needs_rag():
    state = {"messages": [HumanMessage(content="[NEED_RAG] 查资料")]}
    assert route_after_supervisor(state) == "rag"


def test_use_vector_db_routes_to_rag_when_not_done():
    """用户勾选「知识库检索」→ 本轮先检索一次知识库（未完成时）。"""
    state = {
        "messages": [HumanMessage(content="我们的退货政策是什么")],
        "use_vector_db": True,
        "rag_done": False,
    }
    assert route_after_supervisor(state) == "rag"


def test_use_vector_db_no_rerag_after_done():
    """已检索（rag_done=True）后不再路由 rag，避免 rag→execute→supervisor 空转。"""
    state = {
        "messages": [HumanMessage(content="我们的退货政策是什么")],
        "use_vector_db": True,
        "rag_done": True,
    }
    assert route_after_supervisor(state) == "plan"


def test_use_vector_db_off_skips_rag():
    """未勾选时不主动检索。"""
    state = {"messages": [HumanMessage(content="我们的退货政策是什么")], "use_vector_db": False}
    assert route_after_supervisor(state) == "plan"


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


def test_stale_total_tokens_key_does_not_trigger_compress():
    """回归：`token_usage.total_tokens` 全库无写入方（真实键是 `total`），
    曾使压缩触发恒假；该陈旧键即便出现也不得再被当作触发依据。"""
    state = {
        "token_usage": {"total_tokens": 999_999},
        "messages": [HumanMessage(content="短")],
    }
    assert route_after_supervisor(state) == "plan"


def test_too_few_messages_do_not_route_to_compress():
    """上下文超阈值但消息数 ≤ KEEP_RECENT 时不路由压缩（否则空转到 MAX_ITERATIONS）。"""
    big = "x" * 60_000
    messages = [HumanMessage(content=big) for _ in range(6)]
    state = {"messages": messages, "task_plan": [{"id": "t1", "status": "pending"}]}
    assert route_after_supervisor(state) == "execute"
