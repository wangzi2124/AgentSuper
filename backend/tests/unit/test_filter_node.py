"""前置输入过滤节点测试（agent.md §3.2）。

纯语气词/问候语/叹词/致谢/应答/探活（全串锚定）→ 固定模板直接作答，**不调用模型**；
含任务内容或带附件 → 原样放行。
"""

from langchain_core.messages import HumanMessage

from graph.nodes.filter import TOOL_INTRO_TEMPLATE, make_filter_node
from graph.routers import route_after_supervisor

FILLER = ["你好", "hi", "早上好～", "谢谢", "thanks", "OK", "嗯。", "喂你好你好", "test", "Test!!"]
TASKS = ["你好，帮我读取 report.md", "列出目录", "谢谢，另外帮我改下 a.py", "帮我查下这个报错原因", ""]


def test_filler_messages_short_circuit_to_template():
    node = make_filter_node()
    for text in FILLER:
        out = node({"messages": [HumanMessage(content=text)]})
        # 固定模板直接作答：final_output 已就位 → 路由 end；且标记计划完成。
        assert out["final_output"] == TOOL_INTRO_TEMPLATE, text
        assert out["draft_output"] == TOOL_INTRO_TEMPLATE, text
        assert out["task_plan"] == [{"id": "t1", "desc": text, "status": "done"}], text


def test_tasks_pass_through():
    node = make_filter_node()
    for text in TASKS:
        out = node({"messages": [HumanMessage(content=text)]})
        assert out == {}, text


def test_attachments_not_short_circuited():
    blocks = [
        {"type": "text", "text": "你好"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
    ]
    out = make_filter_node()({"messages": [HumanMessage(content=blocks)]})
    assert out == {}


def test_idempotent_when_plan_exists():
    node = make_filter_node()
    state = {"messages": [HumanMessage(content="你好")], "task_plan": [{"id": "t1", "status": "pending"}]}
    assert node(state) == {}
    state2 = {"messages": [HumanMessage(content="你好")], "final_output": "x"}
    assert node(state2) == {}


def test_no_human_message_passes_through():
    assert make_filter_node()({"messages": []}) == {}


def test_template_flow_routes_to_end_without_model():
    node = make_filter_node()
    out = node({"messages": [HumanMessage(content="你好")]})
    state = {"messages": [HumanMessage(content="你好")], "iteration_count": 1, **out}
    # final_output 就位 → 路由直接 end（不经过 plan/execute，不调任何模型）。
    assert route_after_supervisor(state) == "end"
