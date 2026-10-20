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
    "喂",
    "喂你好你好",  # plan.md「及其重复、变体」
    "test",
    "Test!!",
    "你好你好",
    "好的好的",
]

TASKS = [
    "你好，帮我读取 report.md",
    "工作区.txt在哪",
    "列出目录",
    "谢谢，另外帮我改下 a.py",
    "读取 你好.md",
    "在吗？帮我查下这个报错",  # 寒暄词但带任务 → 全串锚定不命中
    "测试一下这个功能",
    "帮我 test 这段代码",
    "",
]


class _StubModel:
    """记录调用并返回预设内容的假 LLM。"""

    tier = None

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[str] = []

    async def ainvoke(self, messages):  # noqa: ANN001
        self.calls.append(messages[0]["content"])

        class _Res:
            content = self._content

            @staticmethod
            def response_metadata():
                return {}

        return _Res()


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


async def test_plan_llm_empty_plan_short_circuits():
    """LLM 按 plan.md 对寒暄输出空计划 `[]` → 短路为草稿，不回退单任务。"""
    # 长于单任务阈值（≥50 字）且带串行信号 → 走 LLM 分解路径；
    # LLM 判定寒暄输出 []（此时三层门禁已放行到模型）。
    msg = "非常感谢你一直以来耐心细致的帮助与支持，辛苦了，然后我们慢慢聊一聊最近的情况吧"
    model = _StubModel("```json\n[]\n```")
    node = make_plan_node(model)
    out = await node({"messages": [HumanMessage(content=msg)]})
    assert out["task_plan"] == [{"id": "t1", "desc": msg, "status": "done"}]
    assert out["draft_output"] == SMALLTALK_REPLIES["greeting"]
    # 仍调了一次 LLM（未命中快速路径才走的分解路径）
    assert len(model.calls) == 1


async def test_plan_llm_parse_failure_falls_back_to_single():
    """解析失败（非 JSON）≠ 显式空计划 → 回退单任务。"""
    model = _StubModel("抱歉，我无法分解该请求。")
    node = make_plan_node(model)
    out = await node({"messages": [HumanMessage(content="帮我查下报错原因")]})
    assert out["task_plan"] == [
        {"id": "t1", "desc": "帮我查下报错原因", "status": "pending"}
    ]
    assert "draft_output" not in out


async def test_plan_llm_model_error_falls_back_to_single():
    class _Boom:
        tier = None

        async def ainvoke(self, messages):  # noqa: ANN001
            raise RuntimeError("model down")

    node = make_plan_node(_Boom())
    out = await node({"messages": [HumanMessage(content="帮我查下报错原因")]})
    assert out["task_plan"] == [
        {"id": "t1", "desc": "帮我查下报错原因", "status": "pending"}
    ]
