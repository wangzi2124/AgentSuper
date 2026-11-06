"""Turn Part Kernel: Tool Parts decide continuation, not provider labels."""

from __future__ import annotations

import json

from harness.turn_parts import ToolPartStatus, TurnPartStore
from models.router import ChatResult, ToolCall


def _result(content: str = "", *, calls: list[ToolCall] | None = None, finish: str = "stop"):
    return ChatResult(content=content, tool_calls=calls or [], finish_reason=finish)


def test_stop_without_parts_is_terminal():
    store = TurnPartStore("build")
    store.begin_step()
    step = store.record_result(_result("hello", finish="stop"))

    assert store.should_continue(step) is False
    assert store.is_terminal is True
    assert store.tool_trace() == []


def test_stop_with_parts_continues():
    store = TurnPartStore("build")
    store.begin_step()
    step = store.record_result(
        _result(
            "",
            calls=[ToolCall(id="c1", name="ls", arguments=json.dumps({"path": "."}))],
            finish="stop",
        )
    )

    assert step.has_parts
    assert store.should_continue(step) is True
    assert store.is_terminal is False


def test_error_and_content_filter_are_terminal_even_with_parts():
    for finish in ("error", "content-filter"):
        store = TurnPartStore("build")
        store.begin_step()
        step = store.record_result(
            _result(
                "",
                calls=[ToolCall(id="c1", name="ls", arguments="{}")],
                finish=finish,
            )
        )
        assert store.should_continue(step) is False


def test_part_lifecycle_emits_matching_start_and_end_events():
    events: list[dict] = []
    store = TurnPartStore("build", emit=events.append)
    store.begin_step()
    part = store.record_tool_call("c1", "read_file", '{"path": "a.txt"}')

    assert part.status is ToolPartStatus.PENDING
    store.start_tool(part)
    assert part.status is ToolPartStatus.RUNNING
    store.finish_tool(part, "content", duration_ms=1.5)

    assert part.status is ToolPartStatus.COMPLETED
    assert [event["type"] for event in events] == ["tool_start", "tool_end"]
    assert events[0]["part_id"] == events[1]["part_id"] == "c1"
    assert events[0]["step_id"] == events[1]["step_id"] == "tool-c1"
    assert events[0]["tool_args"] == {"path": "a.txt"}
    assert events[1]["tool_args"] == {"path": "a.txt"}
    assert events[1]["tool_result"] == "content"


def test_effective_arguments_are_shared_by_trace_and_history():
    store = TurnPartStore("build")
    store.begin_step()
    store.record_result(
        _result(
            "",
            calls=[ToolCall(id="c1", name="read_file", arguments='{"path": "a.txt"}')],
            finish="stop",
        )
    )
    part = store.part("c1")
    assert part is not None
    store.start_tool(part, {"path": "C:/workspace/a.txt"})
    store.finish_tool(part, "content", arguments={"path": "C:/workspace/a.txt"})
    store.finish_step()

    trace = store.tool_trace()[0]
    assert trace["args"] == {"path": "C:/workspace/a.txt"}

    assistant = store.tool_history()[0]
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {
        "path": "C:/workspace/a.txt"
    }
    assert store.tool_history()[1]["content"] == "content"
