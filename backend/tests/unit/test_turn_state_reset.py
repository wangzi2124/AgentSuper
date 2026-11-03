"""每轮状态重置：reducer 通道不能把上一轮结果带进新轮（模型切换追问回归）。

复现过的问题：会话内切换模型后追问「代号和端口分别是什么？」，
plan 判定为空计划 → 本轮无 sub_results → synthesize 从 checkpoint 里
取到上一轮的答案（「收到」）。根因是 `sub_results` 的 `merge_dicts`
reducer 把 `initial_state` 传入的空字典当成 no-op。
"""

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Overwrite

from app.deps import initial_state


def test_initial_state_overwrites_sub_results():
    state = initial_state("项目代号和端口分别是什么？", "u1")
    # Overwrite 绕过 merge_dicts reducer，确保本轮从空结果开始。
    assert isinstance(state["sub_results"], Overwrite)
    assert state["sub_results"].value == {}


def test_initial_state_replaces_messages_with_db_history():
    history = [
        HumanMessage(content="old q", id="um1"),
        AIMessage(content="old a", id="am1"),
    ]
    state = initial_state("new q", "u2", message_id="um2", history_messages=history)

    assert isinstance(state["messages"], Overwrite)
    values = state["messages"].value
    assert [m.content for m in values] == ["old q", "old a", "new q"]
    assert [m.id for m in values] == ["um1", "am1", "um2"]


def test_initial_state_resets_per_turn_plain_channels():
    state = initial_state("第二轮", "u1")
    assert state["task_plan"] == []
    assert state["plan_tier"] == ""
    assert state["draft_output"] == ""
    assert state["final_output"] == ""
    assert state["current_task_id"] == ""
    assert state["iteration_count"] == 0
