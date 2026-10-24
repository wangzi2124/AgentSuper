import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.nodes.base import run_tool_loop
from graph.nodes.execute import _conversation_history
from harness.tool_history import (
    MAX_RESULT_CHARS,
    MAX_TOOL_MESSAGES,
    MAX_TOTAL_CHARS,
    bounded_text,
    cap_tool_history,
    human_count,
    last_human_id,
    make_tool_call_message,
    make_tool_result_message,
    merge_tool_history,
    message_id,
    should_inject,
)
from harness.history import normalize_fallback, normalize_native
from harness.tool_adapter import ToolAdapter
from models.router import ChatResult, ToolCall


class _ScriptedModel:
    tier = "fake"

    def __init__(self, script):
        self.script = list(script)

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return self.script.pop(0)


def _call(call_id="call_1", name="echo", args=None):
    return ChatResult(
        content="",
        tool_calls=[
            ToolCall(
                id=call_id,
                name=name,
                arguments=json.dumps(args or {"text": "hi"}),
            )
        ],
        finish_reason="tool-calls",
    )


def _registry(output="ORION-42"):
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="echo",
            description="echo a value",
            fn=lambda text="": output,
        )
    )
    return registry


@pytest.mark.asyncio
async def test_run_tool_loop_returns_paired_tool_history():
    model = _ScriptedModel([_call(), ChatResult(content="done", finish_reason="stop")])
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "use echo"}],
        _registry(),
        adapter=ToolAdapter(model, mode="native"),
    )

    history = out["tool_history"]
    assert [m["role"] for m in history] == ["assistant", "tool"]
    assert history[0]["tool_calls"][0]["id"] == "call_1"
    assert history[0]["tool_calls"][0]["function"]["name"] == "echo"
    assert history[1]["tool_call_id"] == "call_1"
    assert history[1]["name"] == "echo"
    assert "ORION-42" in history[1]["content"]
    assert all(
        not (m["role"] == "assistant" and not m.get("tool_calls"))
        for m in history
    )


@pytest.mark.asyncio
async def test_run_tool_loop_keeps_multiple_rounds_in_order():
    model = _ScriptedModel(
        [
            _call("c1", args={"text": "one"}),
            _call("c2", args={"text": "two"}),
            ChatResult(content="done", finish_reason="stop"),
        ]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "use echo twice"}],
        _registry(),
        adapter=ToolAdapter(model, mode="native"),
    )

    history = out["tool_history"]
    assert [m["role"] for m in history] == [
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    assert history[0]["tool_calls"][0]["id"] == "c1"
    assert history[1]["tool_call_id"] == "c1"
    assert history[2]["tool_calls"][0]["id"] == "c2"
    assert history[3]["tool_call_id"] == "c2"


@pytest.mark.asyncio
async def test_max_rounds_final_call_still_returns_tool_history():
    model = _ScriptedModel(
        [
            _call("c1"),
            _call("c2"),
            ChatResult(content="final answer", finish_reason="stop"),
        ]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "loop"}],
        _registry(),
        adapter=ToolAdapter(model, mode="native"),
        max_rounds=2,
    )

    assert out["text"] == "final answer"
    assert [m["role"] for m in out["tool_history"]] == [
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]


def test_cap_tool_history_drops_oldest_rounds_and_keeps_pairs():
    messages = []
    for i in range(9):
        messages.append(make_tool_call_message([(f"c{i}", "echo", "{}")]))
        messages.append(make_tool_result_message(f"c{i}", "echo", "x" * 3000))
    capped = cap_tool_history(messages)

    assert len(capped) <= MAX_TOOL_MESSAGES
    assert sum(len(m.get("content") or "") for m in capped) <= MAX_TOTAL_CHARS
    call_ids = {
        c["id"] for m in capped if m["role"] == "assistant" for c in m["tool_calls"]
    }
    tool_ids = {m["tool_call_id"] for m in capped if m["role"] == "tool"}
    assert call_ids == tool_ids


def test_cap_tool_history_truncates_result_and_keeps_args_valid_json():
    huge_args = json.dumps({"text": "a" * 5000})
    messages = [
        make_tool_call_message([("c1", "echo", huge_args)]),
        make_tool_result_message("c1", "echo", "b" * 6000),
    ]
    capped = cap_tool_history(messages)

    result = capped[1]["content"]
    assert len(result) <= MAX_RESULT_CHARS
    assert "truncated" in result
    args = capped[0]["tool_calls"][0]["function"]["arguments"]
    parsed = json.loads(args)
    assert parsed["_truncated"] is True
    assert "preview" in parsed


def test_merge_tool_history_appends_same_turn_and_replaces_new_turn():
    first = [
        make_tool_call_message([("c1", "echo", "{}")]),
        make_tool_result_message("c1", "echo", "one"),
    ]
    second = [
        make_tool_call_message([("c2", "echo", "{}")]),
        make_tool_result_message("c2", "echo", "two"),
    ]

    same, turn, anchor = merge_tool_history(
        first,
        second,
        existing_turn=1,
        new_turn=1,
        existing_anchor="um1",
        new_anchor="um1",
    )
    assert turn == 1 and anchor == "um1"
    assert [m["role"] for m in same] == ["assistant", "tool", "assistant", "tool"]

    replaced, turn, anchor = merge_tool_history(
        first,
        second,
        existing_turn=1,
        new_turn=2,
        existing_anchor="um1",
        new_anchor="um2",
    )
    assert turn == 2 and anchor == "um2"
    assert [m["role"] for m in replaced] == ["assistant", "tool"]
    assert replaced[1]["content"] == "two"

    unchanged, turn, anchor = merge_tool_history(
        first,
        [],
        existing_turn=1,
        new_turn=2,
        existing_anchor="um1",
        new_anchor="um2",
    )
    assert unchanged == first and turn == 1 and anchor == "um1"


def _state_with_tool_history(turn=2, anchor="um2", tool_turn=2):
    messages = [
        HumanMessage(content="first", id="um1"),
        AIMessage(content="ok", id="am1"),
        HumanMessage(content="second", id="um2"),
        AIMessage(content="done", id="am2"),
        HumanMessage(content="current", id="um3"),
    ]
    history = [
        make_tool_call_message([("c1", "echo", '{"text": "hi"}')]),
        make_tool_result_message("c1", "echo", "ORION-42"),
    ]
    return {
        "messages": messages,
        "tool_history": history,
        "tool_history_turn": tool_turn,
        "tool_history_anchor_id": anchor,
    }


def test_conversation_history_injects_after_turn_anchor():
    history = _conversation_history(_state_with_tool_history())
    roles = [m["role"] for m in history]

    assert roles == ["user", "assistant", "user", "assistant", "tool", "assistant"]
    assert history[2]["content"] == "second"
    assert history[3]["tool_calls"][0]["id"] == "c1"
    assert history[4]["content"] == "ORION-42"
    assert history[-1]["content"] == "done"


def test_conversation_history_expires_after_two_turns():
    state = _state_with_tool_history(tool_turn=1)
    state["messages"].append(HumanMessage(content="fourth", id="um4"))
    history = _conversation_history(state)
    assert "ORION-42" not in json.dumps(history, ensure_ascii=False)


def test_conversation_history_does_not_inject_same_turn():
    state = _state_with_tool_history(tool_turn=3)
    history = _conversation_history(state)
    assert "ORION-42" not in json.dumps(history, ensure_ascii=False)


def test_conversation_history_falls_back_when_anchor_missing():
    state = _state_with_tool_history(anchor="missing")
    history = _conversation_history(state)
    assert history[-2]["tool_calls"][0]["id"] == "c1"
    assert history[-1]["content"] == "ORION-42"


def test_injected_history_normalizes_for_native_and_fallback():
    history = _conversation_history(_state_with_tool_history())

    native = normalize_native(history)
    assistant = next(m for m in native if m["role"] == "assistant" and m.get("tool_calls"))
    tool = next(m for m in native if m["role"] == "tool")
    assert assistant["tool_calls"][0]["id"] == tool["tool_call_id"]

    fallback = normalize_fallback(history)
    assert all(m["role"] != "tool" for m in fallback)
    assert all("tool_calls" not in m for m in fallback)
    assert "ORION-42" in json.dumps(fallback, ensure_ascii=False)


def test_bounded_text_short_and_long_limits():
    assert bounded_text("abc", 10) == "abc"
    assert bounded_text("a" * 100, 20).startswith("a")
    assert "truncated" in bounded_text("a" * 100, 20)


def test_cap_single_round_over_slots_keeps_call_result_pairs():
    calls = [(f"c{i}", "echo", "{}") for i in range(20)]
    messages = [make_tool_call_message(calls)]
    messages += [make_tool_result_message(f"c{i}", "echo", "x" * 500) for i in range(20)]

    capped = cap_tool_history(messages)
    call_ids = {
        c["id"] for m in capped if m["role"] == "assistant" for c in m["tool_calls"]
    }
    tool_ids = {m["tool_call_id"] for m in capped if m["role"] == "tool"}
    assert len(capped) <= MAX_TOOL_MESSAGES
    assert call_ids == tool_ids


def test_cap_single_round_shrinks_to_total_budget():
    calls = [(f"c{i}", "echo", "{}") for i in range(9)]
    messages = [make_tool_call_message(calls)]
    messages += [make_tool_result_message(f"c{i}", "echo", "y" * 3000) for i in range(9)]

    capped = cap_tool_history(messages)
    assert sum(len(m.get("content") or "") for m in capped) <= MAX_TOTAL_CHARS


def test_tool_history_id_helpers_support_dicts_and_objects():
    from types import SimpleNamespace

    dicts = [{"role": "user", "id": "u1"}, {"role": "human", "id": "u2"}]
    objects = [SimpleNamespace(type="human", id="u3")]

    assert human_count(dicts + objects) == 3
    assert message_id(dicts[0]) == "u1"
    assert last_human_id(dicts + objects) == "u3"
    assert should_inject(1, 3) is True
    assert should_inject(1, 4) is False
    assert should_inject(3, 3) is False


def test_merge_tool_history_without_existing():
    new = [
        make_tool_call_message([("c1", "echo", "{}")]),
        make_tool_result_message("c1", "echo", "ok"),
    ]
    merged, turn, anchor = merge_tool_history(
        None,
        new,
        existing_turn=None,
        new_turn=1,
        existing_anchor=None,
        new_anchor="u1",
    )
    assert merged == new
    assert turn == 1 and anchor == "u1"
