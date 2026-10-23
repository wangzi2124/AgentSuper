"""L1 单元测试：execute 节点注入会话历史 + 长期记忆（graph/nodes/execute.py）。

真实故障：execute 节点只把 `task.desc` 交给 LLM，从不回灌 `state["messages"]`，
导致同一会话内跨轮追问完全失忆（用户第 1 轮说「项目代号 Falcon-7」，第 2 轮问
「代号是什么」→ 答「我还没看过任何文件，无法确认」）。memory_recall 写入的
`memory_context` 也无人消费。

覆盖：
- 既有对话（human/ai）出现在发给 LLM 的消息里。
- 本回合用户消息不重复注入（避免与 task.desc 重复）。
- 工具角色消息被丢弃（噪音）；压缩摘要以 system 保留。
- `memory_context` 并入 system 段落。
- 预算裁剪：超长历史从最旧处丢弃。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from execution.tool_registry import ToolRegistry
from graph.nodes.execute import _conversation_history, make_execute_node
from models.router import ChatResult


class _CaptureModel:
    """记录每次 ainvoke 收到的消息；首轮即返回纯文本答案。"""

    def __init__(self) -> None:
        self.tier = "fake"
        self.calls: list[list[dict]] = []

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls.append([dict(m) for m in messages])
        return ChatResult(content="ok", finish_reason="stop")


def _state(messages, **extra):
    base = {"messages": messages, "task_plan": [{"id": "t1", "desc": "当前任务", "status": "pending"}]}
    base.update(extra)
    return base


async def test_history_conversation_injected():
    model = _CaptureModel()
    node = make_execute_node(model, ToolRegistry(), system_prompt="SYS")
    state = _state(
        [
            HumanMessage(content="我的项目代号是 Falcon-7"),
            AIMessage(content="已记住 Falcon-7"),
            HumanMessage(content="我的项目代号是什么？"),
        ]
    )
    await node(state)
    sent = model.calls[0]
    roles = [m["role"] for m in sent]
    contents = [m.get("content") for m in sent]
    # system 在最前，历史 human/ai 在中间，当前任务在最后
    assert roles[0] == "system"
    assert "Falcon-7" in contents[1]
    assert "已记住 Falcon-7" in contents[2]
    # 末尾是当前任务，且未把「当前用户消息」重复注入
    assert contents[-1] == "当前任务"
    assert contents.count("我的项目代号是什么？") == 0


async def test_tool_messages_dropped_summary_kept():
    model = _CaptureModel()
    node = make_execute_node(model, ToolRegistry(), system_prompt="SYS")
    state = _state(
        [
            SystemMessage(content="[历史摘要]\n早前对话摘要"),
            HumanMessage(content="读文件"),
            AIMessage(content="好的"),
            ToolMessage(content="巨大的工具输出噪音", tool_call_id="c1"),
            HumanMessage(content="继续"),
        ]
    )
    await node(state)
    sent = model.calls[0]
    joined = "\n".join(str(m.get("content")) for m in sent)
    assert "早前对话摘要" in joined
    assert "巨大的工具输出噪音" not in joined


async def test_memory_context_merged_into_system():
    model = _CaptureModel()
    node = make_execute_node(model, ToolRegistry(), system_prompt="SYS")
    state = _state([HumanMessage(content="问题")], memory_context="- [task_result] 用户偏好中文")
    await node(state)
    sent = model.calls[0]
    assert sent[0]["role"] == "system"
    assert "用户偏好中文" in sent[0]["content"]


async def test_rag_context_merged_into_system():
    """RAG 检索结果（rag_context）此前无人消费，现并入 execute 的 system 段落。"""
    model = _CaptureModel()
    node = make_execute_node(model, ToolRegistry(), system_prompt="SYS")
    state = _state([HumanMessage(content="退款政策是什么？")], rag_context="[来源 1] policy.md\n7天内可退")
    await node(state)
    sent = model.calls[0]
    assert sent[0]["role"] == "system"
    assert "知识库检索结果" in sent[0]["content"]
    assert "7天内可退" in sent[0]["content"]


def test_summary_preferred_when_tail_starts_after_old_summary():
    msgs = [
        SystemMessage(id="s0", content="[历史摘要]\nOLD SUMMARY"),
        HumanMessage(id="h1", content="recent question"),
        AIMessage(id="a1", content="recent answer"),
        HumanMessage(id="h2", content="current question"),
    ]
    hist = _conversation_history(
        {
            "messages": msgs,
            "context_summary": "NEW SUMMARY",
            "context_tail_start_id": "h1",
            "context_bound": True,
        }
    )
    assert hist[0] == {"role": "system", "content": "[历史摘要]\nNEW SUMMARY"}
    joined = "\n".join(h["content"] for h in hist)
    assert "OLD SUMMARY" not in joined
    assert "recent question" in joined
    assert "recent answer" in joined
    assert "current question" not in joined


def test_auto_compressed_summary_kept_with_tail():
    msgs = [
        SystemMessage(id="s0", content="[历史摘要]\nAUTO SUMMARY"),
        HumanMessage(id="h1", content="kept question"),
        AIMessage(id="a1", content="kept answer"),
        HumanMessage(id="h2", content="current question"),
    ]
    hist = _conversation_history(
        {
            "messages": msgs,
            "context_summary": "AUTO SUMMARY",
            "context_tail_start_id": "h1",
            "context_bound": False,
        }
    )
    assert [h for h in hist if h["role"] == "system"] == [
        {"role": "system", "content": "[历史摘要]\nAUTO SUMMARY"}
    ]
    joined = "\n".join(h["content"] for h in hist)
    assert "kept question" in joined
    assert "kept answer" in joined


def test_history_budget_trims_oldest():
    msgs = []
    for i in range(50):
        msgs.append(HumanMessage(content=f"h{i}-" + "x" * 200))
        msgs.append(AIMessage(content=f"a{i}-" + "y" * 200))
    msgs.append(HumanMessage(content="当前"))
    hist = _conversation_history({"messages": msgs}, budget_chars=1000)
    total = sum(len(h["content"]) for h in hist)
    assert total <= 1000 + 400  # 至少丢弃了大部分旧内容
    # 最近的内容保留
    assert any(c["content"].startswith("a49") for c in hist)
