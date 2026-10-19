"""寒暄短路测试：纯问候/致谢/应答不进工具循环（工作区.txt 臆造错误根因修复）。"""

from langchain_core.messages import HumanMessage

from graph.nodes.plan import SMALLTALK_REPLIES, classify_smalltalk, make_plan_node
from graph.nodes.synthesize import make_synthesize_node
from graph.routers import route_after_plan, route_after_supervisor

GREETINGS = [
    "你好",
    "您好！",
    "hi",
    "Hello!",
    "在吗？",
    "早上好",
    "晚上好～",
    "谢谢",
    "thanks",
    "OK",
    "嗯。",
    "收到了",
]

TASKS = [
    "你好，帮我读取 report.md",
    "工作区.txt在哪",
    "列出目录",
    "谢谢，另外帮我改下 a.py",
    "读取 你好.md",
    "在吗？帮我查下这个报错",  # 寒暄词但带任务 → 全串锚定不命中
    "",
]


def test_classify_greetings():
    for t in GREETINGS:
        assert classify_smalltalk(t) is not None, t


def test_classify_tasks_unaffected():
    for t in TASKS:
        assert classify_smalltalk(t) is None, t


async def test_plan_short_circuits_greeting():
    node = make_plan_node(None)
    out = await node({"messages": [HumanMessage(content="你好")]})
    assert out["draft_output"] == SMALLTALK_REPLIES["greeting"]
    assert out["task_plan"] == [{"id": "t1", "desc": "你好", "status": "done"}]


async def test_plan_normal_task_unaffected():
    node = make_plan_node(None)
    out = await node({"messages": [HumanMessage(content="读取 a.txt")]})
    assert "draft_output" not in out
    assert out["task_plan"] == [{"id": "t1", "desc": "读取 a.txt", "status": "pending"}]


async def test_plan_image_turn_not_shortcircuited():
    blocks = [
        {"type": "text", "text": "你好"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
    ]
    node = make_plan_node(None)
    out = await node({"messages": [HumanMessage(content=blocks)]})
    assert "draft_output" not in out
    assert out["task_plan"][0]["status"] == "pending"


async def test_greeting_flow_routes_to_synthesize():
    node = make_plan_node(None)
    plan_out = await node({"messages": [HumanMessage(content="你好")]})
    state = {
        "messages": [HumanMessage(content="你好")],
        "iteration_count": 1,
        **plan_out,
    }
    # 无待办 → execute 空转；supervisor 看到草稿 → synthesize；单任务直取草稿为终稿
    assert route_after_plan(state) == "execute_subagent"
    assert route_after_supervisor(state) == "synthesize"
    final = await make_synthesize_node(None)(state)
    assert final["final_output"] == SMALLTALK_REPLIES["greeting"]
