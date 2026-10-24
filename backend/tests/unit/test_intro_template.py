"""自我介绍类输入走固定模板，零模型调用。"""

from langchain_core.messages import HumanMessage

from graph.nodes.filter import TOOL_INTRO_TEMPLATE, make_filter_node
from graph.nodes.plan import classify_smalltalk
from graph.routers import route_after_supervisor

INTROS = [
    "简单介绍",
    "简单介绍一下",
    "简单介绍一下你自己",
    "介绍一下你自己",
    "介绍你自己",
    "你是谁",
    "你能做什么",
    "你有什么功能",
    "怎么用",
    "自我介绍",
]

NOT_INTROS = [
    "介绍一下这个文件",
    "简单介绍下这个项目",
    "介绍一下 report.md",
    "你能做什么，帮我读 a.txt",
    "这个工具怎么用",
]


def test_classify_intro():
    for text in INTROS:
        assert classify_smalltalk(text) == "intro", text


def test_non_intro_tasks_are_not_short_circuited():
    for text in NOT_INTROS:
        assert classify_smalltalk(text) is None, text


def test_filter_returns_template_without_model():
    node = make_filter_node()
    for text in INTROS:
        out = node({"messages": [HumanMessage(content=text)]})
        assert out["final_output"] == TOOL_INTRO_TEMPLATE, text
        assert out["draft_output"] == TOOL_INTRO_TEMPLATE, text

    state = {
        "messages": [HumanMessage(content="简单介绍一下你自己")],
        "iteration_count": 1,
        **node({"messages": [HumanMessage(content="简单介绍一下你自己")]}),
    }
    assert route_after_supervisor(state) == "end"
