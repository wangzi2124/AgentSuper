from __future__ import annotations

from cognition.tool_output import (
    PrunePolicy,
    ToolOutputPruner,
    prune_tool_outputs,
)


def _round(call_id: str, name: str = "read_file") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": call_id, "function": {"name": name, "arguments": "{}"}}
        ],
    }


def _tool(call_id: str, name: str = "read_file", content: str | None = None) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "tool_name": name,
        "content": content if content is not None else "x" * 400,
        "_id": f"m-{call_id}",
    }


def _messages(name: str = "read_file") -> list[dict]:
    return [
        _round("c1", name),
        _tool("c1", name),
        _round("c2", name),
        _tool("c2", name),
    ]


def test_pruner_returns_small_result_with_pruned_tokens():
    out = ToolOutputPruner().prune(
        _messages(),
        PrunePolicy(protect_tokens=1, minimum_tokens=1, tail_turns=1),
    )

    assert out.applied is True
    assert out.pruned_tokens > 0
    assert out.messages[1]["content"].startswith("[tool output pruned")
    assert out.records[0]["index"] == 1
    assert out.messages[3]["content"] == "x" * 400


def test_pruner_is_idempotent():
    policy = PrunePolicy(protect_tokens=1, minimum_tokens=1, tail_turns=1)
    first = ToolOutputPruner().prune(_messages(), policy)
    second = ToolOutputPruner().prune(first.messages, policy)

    assert second.applied is False
    assert second.pruned_tokens == 0


def test_pruner_respects_protected_tools():
    out = ToolOutputPruner().prune(
        _messages("skill"),
        PrunePolicy(protect_tokens=1, minimum_tokens=1, tail_turns=1),
    )

    assert out.applied is False
    assert out.messages[1]["content"] == "x" * 400


def test_pruner_gate_skips_small_context():
    out = ToolOutputPruner().prune(
        _messages(),
        PrunePolicy(
            protect_tokens=1,
            minimum_tokens=1,
            tail_turns=1,
            gate_tokens=10_000_000,
        ),
    )

    assert out.applied is False
    assert out.messages[1]["content"] == "x" * 400


def test_pruner_disabled_returns_input():
    messages = _messages()
    out = ToolOutputPruner().prune(messages, PrunePolicy(enabled=False))

    assert out.messages is messages
    assert out.applied is False


def test_pruner_persists_records_once(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        "app.session.repository.mark_tool_pruned",
        lambda *args: calls.append(args),
    )

    out = ToolOutputPruner(session_id="s1").prune(
        _messages(),
        PrunePolicy(protect_tokens=1, minimum_tokens=1, tail_turns=1),
    )

    assert out.applied is True
    assert calls == [("s1", "m-c1", out.records[0]["path"], out.records[0]["tokens"])]


def test_pure_pruner_honors_custom_protected_tools():
    out = prune_tool_outputs(
        _messages("custom"),
        protect_tokens=1,
        minimum_tokens=1,
        tail_turns=1,
        protected_tools=frozenset({"custom"}),
    )

    assert out[1]["content"] == "x" * 400
