"""Unit tests: stable prompt head, file memoization, Anthropic cache hints."""

from __future__ import annotations

import hashlib
import os
import time

from langchain_core.messages import HumanMessage

from models import context_guard
from models.cache_hints import apply_cache_hints
from models.context_guard import FitResult
from models.router import TierModel
from prompts.assembly import compose_system, stable_head
from prompts.loader import PromptLoader

# Golden hashes for the cacheable head. Update only when the stable prompt head
# is intentionally changed; volatile turn content must never move before it.
_GOLDEN_HEAD = {
    ("file_agent", "openai/gpt-4o"): "1e618cb918db754cde6314a487be407e98795254208235ed0db2177e4b905733",
    ("supervisor", "deepseek/deepseek-v4-pro"): "6cd7dc2a7a4d7cbfb75a9ad503a64414a87292fe7cc535068ccd2e6bfbfeefb7",
}


def test_stable_head_golden_hashes():
    for (agent, model), expected in _GOLDEN_HEAD.items():
        text = stable_head(agent, model)
        actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert actual == expected, f"stable head changed: {agent} / {model}"


def test_volatile_turn_context_stays_after_stable_head():
    head = stable_head("file_agent", "openai/gpt-4o")
    volatile = compose_system(
        "file_agent",
        "openai/gpt-4o",
        vision_prefix="VISION",
        memory="MEMORY",
        rag="RAG",
        extra="SKILLS",
    )
    assert volatile.startswith(head)
    assert volatile[len(head):].lstrip().startswith("VISION")


def test_prompt_loader_memoizes_and_reloads_on_mtime(tmp_path):
    path = tmp_path / "role.md"
    path.write_text("A", encoding="utf-8")
    loader = PromptLoader(base_dir=tmp_path)
    assert loader.raw("role.md") == "A"

    path.write_text("B", encoding="utf-8")
    bumped = time.time() + 2
    os.utime(path, (bumped, bumped))
    assert loader.raw("role.md") == "B"


def test_compose_system_keeps_stable_head_first():
    text = compose_system(
        "supervisor",
        "openai/gpt-4o",
        head_override="HEAD",
        vision_prefix="VISION",
        memory="MEM",
        rag="RAG",
        extra="SKILLS",
    )
    assert text.startswith("HEAD")
    assert text.index("VISION") > text.index("HEAD")
    assert text.index("MEM") > text.index("VISION")
    assert text.index("RAG") > text.index("MEM")
    assert text.index("SKILLS") > text.index("RAG")


def test_anthropic_hints_mark_system_and_latest_user_only():
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "latest"},
    ]
    out = apply_cache_hints(messages, "anthropic/claude-sonnet")
    assert out is not messages
    assert isinstance(out[0]["content"], list)
    assert out[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert out[1]["content"] == "first"
    assert isinstance(out[3]["content"], list)
    assert out[3]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    # Input is not mutated.
    assert messages[0]["content"] == "SYS"


def test_non_anthropic_providers_keep_messages_untouched():
    messages = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
    assert apply_cache_hints(messages, "deepseek/deepseek-v4-flash") is messages
    assert apply_cache_hints(messages, "ollama_chat/qwen2.5:3b") is messages


def test_prepare_call_applies_anthropic_hints(monkeypatch):
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "latest"},
    ]
    monkeypatch.setattr(
        context_guard,
        "fit_messages",
        lambda mid, rendered, tools=None: FitResult(model=mid, messages=rendered),
    )
    model = TierModel(tier="auto")
    monkeypatch.setattr(
        model,
        "_endpoint",
        lambda: {
            "model": "anthropic/claude-sonnet",
            "api_base": None,
            "api_key": "k",
        },
    )
    kwargs, _, _ = model._transport_kwargs(messages, None)
    assert isinstance(kwargs["messages"][0]["content"], list)
    assert kwargs["messages"][0]["content"][-1]["cache_control"] == {
        "type": "ephemeral"
    }
    assert isinstance(kwargs["messages"][1]["content"], list)


def test_openai_gets_prompt_cache_key_and_session_affinity(monkeypatch):
    from models import runtime

    token = runtime.set_request_session("session-1")
    try:
        model = TierModel(tier="auto")
        monkeypatch.setattr(
            model,
            "_endpoint",
            lambda: {"model": "openai/gpt-4o", "api_base": None, "api_key": "k"},
        )
        kwargs, _, _ = model._transport_kwargs(
            [{"role": "user", "content": "hi"}], None
        )
        assert kwargs["extra_body"]["prompt_cache_key"] == "session-1"
        assert kwargs["extra_headers"]["x-session-affinity"] == "session-1"
    finally:
        runtime.reset_request_session(token)


def test_deepseek_and_ollama_do_not_get_openai_cache_key(monkeypatch):
    from models import runtime

    token = runtime.set_request_session("session-1")
    try:
        for endpoint in (
            {"model": "deepseek/deepseek-v4-flash", "api_base": None, "api_key": "k"},
            {"model": "ollama_chat/qwen2.5:3b", "api_base": None, "api_key": "ollama"},
        ):
            model = TierModel(tier="auto")
            monkeypatch.setattr(model, "_endpoint", lambda ep=endpoint: ep)
            kwargs, _, _ = model._transport_kwargs(
                [{"role": "user", "content": "hi"}], None
            )
            assert "prompt_cache_key" not in (kwargs.get("extra_body") or {})
            assert "x-session-affinity" not in (kwargs.get("extra_headers") or {})
    finally:
        runtime.reset_request_session(token)


def test_execute_stable_head_stays_first():
    import asyncio

    from execution.tool_registry import ToolRegistry
    from graph.nodes.execute import make_execute_node

    class _Capture:
        tier = "fake"

        def __init__(self) -> None:
            self.calls: list[list[dict]] = []

        async def ainvoke(self, messages, *, tools=None, **kwargs):
            self.calls.append([dict(m) for m in messages])
            from models.router import ChatResult

            return ChatResult(content="ok", finish_reason="stop")

    model = _Capture()
    node = make_execute_node(model, ToolRegistry(), system_prompt="SYS")
    state = {
        "messages": [HumanMessage(content="问题", id="h1")],
        "task_plan": [{"id": "t1", "desc": "问题", "status": "pending"}],
        "memory_context": "- [fact] MEM",
        "rag_context": "[来源] RAG",
    }
    asyncio.run(node(state))
    system = model.calls[0][0]["content"]
    assert system.startswith("SYS")
    assert system.index("MEM") > 3
    assert system.index("RAG") > system.index("MEM")
