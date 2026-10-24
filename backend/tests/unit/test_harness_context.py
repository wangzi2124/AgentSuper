from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from harness.context import assemble_model_view


def _text(value) -> str:
    return str(value or "")


def test_summary_then_tail_then_current_is_excluded():
    state = {
        "messages": [
            HumanMessage(content="old", id="h1"),
            AIMessage(content="old answer", id="a1"),
            HumanMessage(content="recent", id="h2"),
            AIMessage(content="recent answer", id="a2"),
            HumanMessage(content="current", id="h3"),
        ],
        "context_summary": "summary text",
        "context_tail_start_id": "h2",
        "context_window": 8_000,
    }

    view = assemble_model_view(state, content_text=_text)

    assert view[0]["role"] == "system"
    assert "[历史摘要]" in view[0]["content"]
    assert [m["content"] for m in view[1:]] == ["recent", "recent answer"]
    assert "current" not in [m["content"] for m in view]


def test_budget_never_drops_summary():
    state = {
        "messages": [
            HumanMessage(content="x" * 10_000, id="h1"),
            AIMessage(content="y" * 10_000, id="a1"),
            HumanMessage(content="current", id="h2"),
        ],
        "context_summary": "must stay",
        "context_bound": True,
        "context_window": 8_000,
    }

    view = assemble_model_view(state, content_text=_text, budget_chars=100)

    assert view[0]["role"] == "system"
    assert "must stay" in view[0]["content"]


def test_empty_messages_returns_empty_view():
    assert assemble_model_view({"messages": []}, content_text=_text) == []
