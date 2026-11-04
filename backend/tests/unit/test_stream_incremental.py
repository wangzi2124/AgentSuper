"""Unit tests: streaming text is emitted incrementally without leaking tool JSON."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from models import stream as stream_mod
from models.stream import collect_stream, stream_chat


def _chunk(content=None, reasoning=None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
        tool_calls=tool_calls,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)],
        usage=None,
    )


async def _gen(parts):
    for part in parts:
        yield _chunk(part)


@pytest.mark.asyncio
async def test_prose_is_emitted_incrementally():
    seen: list[str] = []
    result = await collect_stream(
        _gen(["Hel", "lo", " wor", "ld"]), on_text=seen.append
    )
    assert result.content == "Hello world"
    assert seen == ["Hel", "lo", " wor", "ld"]


@pytest.mark.asyncio
async def test_tool_call_json_is_not_streamed():
    seen: list[str] = []
    payload = '{"name": "read_file", "arguments": {"path": "a"}}'
    result = await collect_stream(
        _gen([payload[:12], payload[12:]]),
        tool_names={"read_file"},
        on_text=seen.append,
    )
    assert seen == []
    assert result.content == ""
    assert result.tool_calls[0].function.name == "read_file"


@pytest.mark.asyncio
async def test_unknown_tool_reply_is_streamed_once():
    seen: list[str] = []
    payload = '{"name": "unknown", "arguments": {"message": "hello"}}'
    result = await collect_stream(_gen([payload]), on_text=seen.append)
    assert result.content == "hello"
    assert seen == ["hello"]


async def _fake_acompletion(**kwargs):
    if kwargs.get("stream"):
        async def body():
            yield _chunk("你")
            raise RuntimeError("stream dropped")

        return body()
    msg = SimpleNamespace(
        content="你完整回答", tool_calls=[], reasoning_content=None
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason="stop")],
        usage=None,
    )


@pytest.mark.asyncio
async def test_interrupted_stream_does_not_replay_text(monkeypatch):
    monkeypatch.setattr(stream_mod.litellm, "acompletion", _fake_acompletion)
    seen: list[str] = []
    result = await stream_chat(
        [{"role": "user", "content": "hi"}],
        model="fake/model",
        on_text=seen.append,
    )
    # The partial chunk was already streamed; the non-stream retry must not
    # replay the full answer on top of it.
    assert seen == ["你"]
    assert result.content == "你完整回答"
