"""L1：opencode 对齐的压缩选择 / 兜底 / 剪枝。"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from cognition.compressor import build_fallback_summary
from cognition.tool_output import prune_tool_outputs
from graph.routers import route_after_supervisor
from harness.compression import _role, select_tail_messages


def test_select_tail_keeps_recent_turns():
    messages = [
        HumanMessage(content="u1", id="u1"),
        AIMessage(content="a1", id="a1"),
        HumanMessage(content="u2", id="u2"),
        AIMessage(content="a2", id="a2"),
        HumanMessage(content="u3", id="u3"),
        AIMessage(content="a3", id="a3"),
    ]
    head, tail = select_tail_messages(messages, keep_tokens=10_000, tail_turns=2)
    assert [m.id for m in tail] == ["u2", "a2", "u3", "a3"]
    assert [m.id for m in head] == ["u1", "a1"]


def test_select_tail_split_never_starts_with_orphan_tool():
    messages = [
        HumanMessage(content="u1", id="u1"),
        AIMessage(content="a1", id="a1"),
        {"role": "tool", "content": "x", "tool_call_id": "c1"},
    ]
    head, tail = select_tail_messages(messages, keep_tokens=4, tail_turns=1)
    assert tail and _role(tail[0]) != "tool"
    assert head


def test_fallback_summary_is_structured_and_keeps_precision():
    text = build_fallback_summary(
        [HumanMessage(content="目标：对比 /work/a.py 与端口 8080")]
    )
    assert "## Goal" in text
    assert "/work/a.py" in text
    assert "8080" in text


def test_prune_protects_skill_tool_output(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "compaction_prune", True)
    messages = [
        {"role": "user", "content": "u1"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c2", "function": {"name": "read_file", "arguments": "{}"}}],
        },
        {"role": "tool", "content": "y" * 100_000, "tool_call_id": "c2"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "skill", "arguments": "{}"}}],
        },
        {"role": "tool", "content": "x" * 100_000, "tool_call_id": "c1"},
        {"role": "user", "content": "u2"},
        {"role": "user", "content": "u3"},
    ]
    out = prune_tool_outputs(messages, protect_tokens=0, minimum_tokens=0, tail_turns=1)
    assert out[2]["content"].startswith("[tool output pruned")
    assert out[4]["content"] == "x" * 100_000


def test_strip_media_blocks_for_overflow_retry():
    from graph.nodes.base import _strip_media_blocks

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}},
            ],
        }
    ]
    out, stripped = _strip_media_blocks(messages)
    assert stripped is True
    assert out[0]["content"][0] == {"type": "text", "text": "看图"}
    assert out[0]["content"][1]["type"] == "text"


def test_router_respects_compaction_auto(monkeypatch):
    from config import settings

    state = {
        "messages": [HumanMessage(content="x") for _ in range(10)],
        "compress_threshold": 1,
        "context_compressed": False,
        "task_plan": [{"status": "pending"}],
        "iteration_count": 0,
    }
    monkeypatch.setattr(settings, "compaction_auto", False)
    assert route_after_supervisor(state) != "compress"
    monkeypatch.setattr(settings, "compaction_auto", True)
    assert route_after_supervisor(state) == "compress"
