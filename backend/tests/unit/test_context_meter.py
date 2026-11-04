from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from cognition.context_meter import ContextMeter
from harness.compression import CompressionPlan, select_tail_messages, summarize_messages


class _Capture:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def ainvoke(self, messages):
        self.prompts.append(str(messages[-1].get("content") or ""))
        return type("R", (), {"content": "summary"})()


class _FakeTier:
    def __init__(self) -> None:
        self.tier = "fake"

    def model_name(self) -> str:
        return "fake/model"


def test_media_is_counted_by_block_not_base64_length():
    small = HumanMessage(
        content=[
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
        id="m1",
    )
    large = HumanMessage(
        content=[
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 200_000}},
        ],
        id="m1",
    )
    meter = ContextMeter()
    a = meter.estimate([small])
    b = meter.estimate([large])
    assert a.total == b.total
    assert a.media == b.media == 1_600
    assert a.total < 2_000


def test_meter_counts_system_and_tool_schemas():
    meter = ContextMeter()
    messages = [HumanMessage(content="hello", id="m1")]
    bare = meter.estimate(messages)
    sized = meter.estimate(
        messages,
        system="你是一个严格的代码助手。" * 50,
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
    )
    assert sized.total > bare.total
    assert sized.system > 0
    assert sized.tools > 0


def test_provider_tokenizer_selection(monkeypatch):
    from cognition import token_counter

    calls = {"deepseek": 0, "tiktoken": 0}

    def deepseek(text):
        calls["deepseek"] += 1
        return 7

    def tiktoken(text):
        calls["tiktoken"] += 1
        return 3

    monkeypatch.setattr(token_counter, "estimate_tokens_deepseek", deepseek)
    monkeypatch.setattr(token_counter, "estimate_tokens_tiktoken", tiktoken)

    meter = ContextMeter()
    messages = [{"role": "user", "content": "hello"}]
    meter.estimate(messages, model_id="deepseek/deepseek-v4-flash")
    meter.estimate(messages, model_id="ollama_chat/qwen2.5:3b")
    assert calls["deepseek"] > 0
    assert calls["tiktoken"] > 0


def test_truncate_uses_provider_counter(monkeypatch):
    from cognition import token_counter

    calls = {"n": 0}

    def tiktoken(text):
        calls["n"] += 1
        return 10_000

    monkeypatch.setattr(token_counter, "estimate_tokens_tiktoken", tiktoken)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "x" * 1000},
    ]
    token_counter.truncate_messages(messages, max_tokens=100, model_id="ollama_chat/x")
    assert calls["n"] > 0


def test_per_model_calibration_scales_estimate_and_resets():
    meter = ContextMeter()
    messages = [HumanMessage(content="hello world", id="m1")]
    base = meter.estimate(messages, model_id="ollama_chat/x").total
    meter.observe_model("ollama_chat/x", 100, 150)
    scaled = meter.estimate(messages, model_id="ollama_chat/x").total
    assert meter.ratio("ollama_chat/x") == pytest.approx(1.5)
    assert scaled > base
    # Other models keep their own (uncorrected) denominator.
    assert (
        meter.estimate(messages, model_id="deepseek/deepseek-v4-flash").total == base
    )
    meter.reset_calibration()
    assert meter.ratio("ollama_chat/x") == pytest.approx(1.0)


def test_cache_is_content_aware_without_ids():
    meter = ContextMeter()
    short = {"role": "user", "content": "short"}
    long = {"role": "user", "content": "x" * 500}
    short_est = meter.estimate([short], use_cache=True).total
    long_est = meter.estimate([long], use_cache=True).total
    assert long_est > short_est


def test_calibration_invalidates_cached_estimates():
    meter = ContextMeter()
    messages = [HumanMessage(content="hello world", id="m1")]
    before = meter.estimate(messages, model_id="m", use_cache=True).total
    meter.observe_model("m", 100, 200)
    after = meter.estimate(messages, model_id="m", use_cache=True).total
    assert after > before


@pytest.mark.parametrize(
    "window,usable,preserve",
    [
        (8_192, 6_144, 2_000),
        (16_384, 12_288, 3_072),
        (32_768, 24_576, 6_144),
        (65_536, 57_344, 8_000),
        (128_000, 119_808, 8_000),
    ],
)
def test_plan_table_for_unknown_output(window, usable, preserve):
    plan = CompressionPlan.for_window(window)
    assert plan.threshold == usable
    assert plan.keep_tokens == preserve
    assert plan.threshold > plan.keep_tokens
    assert plan.tail_turns == 2


def test_internal_id_never_reaches_provider_and_tail_reads_it():
    from harness.compression import tail_start_id
    from models.router import _render_message

    payload = _render_message({"role": "user", "content": "hi", "_id": "um1"})
    assert payload == {"role": "user", "content": "hi"}
    assert tail_start_id([{"role": "user", "content": "hi", "_id": "um1"}]) == "um1"


def test_zero_tail_turns_means_no_tail():
    messages = [HumanMessage(content="a", id="m1"), AIMessage(content="b", id="m2")]
    head, tail = select_tail_messages(messages, keep_tokens=10_000, tail_turns=0)
    assert tail == []
    assert head == messages


@pytest.mark.asyncio
async def test_summary_prompt_strips_media_and_previous_summary_once():
    model = _Capture()
    messages = [
        HumanMessage(
            content=[
                {"type": "text", "text": "看这张图 /work/a.py"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "B" * 10_000}},
            ],
            id="m1",
        ),
        HumanMessage(
            content="[历史摘要]\nOLD SUMMARY",
            id="old_summary",
            additional_kwargs={"compaction": True},
        ),
        AIMessage(content="done", id="m2"),
    ]
    out = await summarize_messages(
        messages,
        existing_summary="OLD SUMMARY",
        model=model,
        segment_tokens=10**9,
    )
    assert out == "summary"
    prompt = model.prompts[0]
    assert "data:image/png;base64" not in prompt
    assert "[图片]" in prompt
    assert prompt.count("OLD SUMMARY") == 1
