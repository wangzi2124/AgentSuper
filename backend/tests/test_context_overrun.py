# -*- coding: utf-8 -*-
"""[C5] 长任务上下文越界根治测试：硬护栏预算、自适应估算校正、工具输出 token 封顶、
STEP_STATE 落盘。

覆盖：
  - budget.py：llm_call_budget（usable×safety_ratio）、compaction_target_tokens
  - token_counter.py：set/update_token_correction（EMA + 钳制）
  - core._llm_call：用实际 usage 自适应校准估算系数（流式 + 非流式回退）
  - generate.py：截断目标使用 llm_call_budget；多轮执行写 STEP_STATE
  - tool_output.py：bound_tool_output 按 token 封顶（超限写盘 + 续读提示）
  - step_state.py：写/读 latest STEP_STATE
运行：pytest tests/test_context_overrun.py
"""
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.config import settings
from app.context import budget
from app.context import token_counter as tc
from app.context import tool_output
from app.context import step_state


# ── budget ─────────────────────────────────────────────────────────────────

def test_llm_call_budget(monkeypatch):
    monkeypatch.setattr(settings, "max_context_tokens", 100_000)
    monkeypatch.setattr(settings, "context_reserve_tokens", 10_000)
    monkeypatch.setattr(settings, "context_safety_ratio", 0.9)
    assert budget.usable_context_tokens() == 90_000
    assert budget.llm_call_budget() == 81_000  # ×0.9 安全垫
    monkeypatch.setattr(settings, "context_safety_ratio", 1.0)
    assert budget.llm_call_budget() == 90_000
    monkeypatch.setattr(settings, "context_safety_ratio", 0.0)  # 钳制到 0.1
    assert budget.llm_call_budget() == 9_000


def test_compaction_target_tokens(monkeypatch):
    monkeypatch.setattr(settings, "max_context_tokens", 100_000)
    monkeypatch.setattr(settings, "context_reserve_tokens", 10_000)
    monkeypatch.setattr(settings, "compaction_target_ratio", 0.5)
    assert budget.compaction_target_tokens() == 45_000


# ── 自适应估算校正 ─────────────────────────────────────────────────────────

def test_set_and_update_correction(monkeypatch):
    monkeypatch.setattr(settings, "token_estimate_correction", 1.13)
    tc._correction = None
    assert tc.token_correction_factor() == 1.13
    # 低估（实际 1.3x 估算）→ 系数上修
    tc.update_token_correction(100, 130)
    assert tc.token_correction_factor() > 1.13
    # EMA 收敛但被钳制
    tc.set_token_correction_factor(5.0)
    assert tc.token_correction_factor() == 2.5
    tc.set_token_correction_factor(0.5)
    assert tc.token_correction_factor() == 1.0
    # 无效输入不更新
    before = tc.token_correction_factor()
    tc.update_token_correction(0, 50)
    tc.update_token_correction(50, 0)
    assert tc.token_correction_factor() == before


def test_estimate_scales_with_correction():
    base = tc.estimate_tokens("hello world")
    tc.set_token_correction_factor(2.0)
    try:
        assert tc.estimate_tokens("hello world") == base * 2
    finally:
        tc.set_token_correction_factor(1.13)


# ── core._llm_call 自适应校准 ──────────────────────────────────────────────

def _chunk(content=None, tool_calls=None, finish_reason=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)], usage=usage)


@pytest.mark.asyncio
async def test_llm_call_updates_correction(monkeypatch):
    import app.agent.graphmod.core as core_mod
    from _graphmod_support import build_agent

    async def fake_acompletion(**kw):
        async def gen():
            yield _chunk(content="hi")
            yield _chunk(usage=SimpleNamespace(prompt_tokens=150, completion_tokens=5,
                        prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=150))
        return gen()
    monkeypatch.setattr(core_mod.litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(core_mod, "record_model_call", lambda *a, **k: None)
    monkeypatch.setattr(core_mod, "trace", lambda *a, **k: None)
    monkeypatch.setattr(core_mod, "trace_messages", lambda *a, **k: None)
    monkeypatch.setattr(core_mod, "log_prompt", lambda *a, **k: None)

    tc.set_token_correction_factor(1.13)
    before = tc.token_correction_factor()
    agent = build_agent()
    await agent._llm_call("m", [{"role": "user", "content": "x"}], None)
    assert tc.token_correction_factor() > before  # 实际 150 > 估算 → 系数上修
    tc.set_token_correction_factor(1.13)


@pytest.mark.asyncio
async def test_assemble_response_updates_correction(monkeypatch):
    import app.agent.graphmod.core as core_mod
    from _graphmod_support import build_agent
    monkeypatch.setattr(core_mod, "record_model_call", lambda *a, **k: None)
    monkeypatch.setattr(core_mod, "trace", lambda *a, **k: None)
    tc.set_token_correction_factor(1.13)
    before = tc.token_correction_factor()
    agent = build_agent()
    agent._last_call_estimate = 100
    resp = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=3,
                              prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=120),
        choices=[SimpleNamespace(message=SimpleNamespace(content="a"))],
    )
    agent._assemble_response("m", resp, 0.0, None)
    assert tc.token_correction_factor() > before
    tc.set_token_correction_factor(1.13)


# ── generate：截断目标用 llm_call_budget + STEP_STATE 落盘 ─────────────────

@pytest.mark.asyncio
async def test_generate_truncates_to_llm_call_budget(monkeypatch, tmp_path):
    from _graphmod_support import FakeLLM, build_agent, make_state
    import app.agent.graphmod.generate as gen_mod
    monkeypatch.setattr(settings, "max_context_tokens", 10_000)
    monkeypatch.setattr(settings, "context_reserve_tokens", 1_000)
    monkeypatch.setattr(settings, "context_safety_ratio", 0.5)  # budget = 4500
    for n in ("record_model_call", "trace", "trace_messages"):
        monkeypatch.setattr(gen_mod, n, lambda *a, **k: None)

    agent = build_agent()
    monkeypatch.setattr(agent, "_build_tool_defs", lambda *a, **k: [])  # 无 schema 开销
    llm = FakeLLM()
    llm.responses = [
        FakeLLM().response(tool_calls=[("tool_read_file", '{"path": "/x"}')]),
        FakeLLM().response(tool_calls=[("tool_read_file", '{"path": "/x"}')]),
        FakeLLM().response(content="done"),
    ]
    sent_snapshots = []

    async def snap_llm(model, messages, tool_defs, state=None):
        sent_snapshots.append([dict(m) for m in messages])  # 发送时快照（原列表后续会被改写）
        return await llm(model, messages, tool_defs, state=state)
    agent._llm_call = snap_llm

    exec_calls = []

    async def spy(name, args, state=None):
        exec_calls.append(name)
        return "大内容" * 5000  # 中文内容远超预算
    agent._execute_tool = spy

    out = await agent._generate(make_state(_cwd=str(tmp_path)))
    assert out["answer"] == "done"
    # 每轮发给 LLM 的消息估算 ≤ llm_call_budget（4500）——超限工具输出被截断/丢弃
    for msgs in sent_snapshots:
        est = tc.estimate_tokens_messages(msgs)
        assert est <= 4500 + 300, est
    # STEP_STATE 已落盘（第二轮执行后）
    step_files = list((tmp_path / ".agents" / "steps").glob("*.md"))
    assert any(p.name == "latest.md" for p in step_files)


# ── tool_output token 封顶 ─────────────────────────────────────────────────

def test_bound_tool_output_token_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "tool_output_max_tokens", 500)
    monkeypatch.setattr(tool_output, "_truncation_dir", lambda: tmp_path)
    big = "中文字符内容" * 5000  # 估算 > 500 token
    out = tool_output.bound_tool_output(big, "tool_read_file")
    assert tc.estimate_tokens(out) <= 500 + 100
    assert "output truncated" in out
    # 原文已写盘（续读不丢信息）
    saved = list(tmp_path.glob("tool_*.txt"))
    assert saved and saved[0].read_text(encoding="utf-8") == big


def test_bound_tool_output_no_cap(monkeypatch):
    monkeypatch.setattr(settings, "tool_output_max_tokens", 0)
    out = tool_output.bound_tool_output("small")
    assert out == "small"


# ── step_state ─────────────────────────────────────────────────────────────

def test_step_state_write_read(tmp_path):
    path = step_state.write_step_state(str(tmp_path), 1, {
        "objective": "实现 x", "completed": ["round 1: tool_ls"], "files": ["a.py"],
    })
    assert path and path.endswith("0001.md")
    seq, body = step_state.load_latest_step_state(str(tmp_path))
    assert seq == 1
    assert "实现 x" in body and "a.py" in body
    # 无会话目录 → None
    assert step_state.write_step_state("", 1, {}) is None
    assert step_state.load_latest_step_state("") == (None, None)


def test_step_state_no_dir(tmp_path):
    assert step_state.load_latest_step_state(str(tmp_path / "nonexistent")) == (None, None)


# ── 方案 D · 小步快走摘要替换 ──────────────────────────────────────────────

class FakeSummarizer:
    def __init__(self, **kw):
        self.kw = kw

    async def apply(self, history):
        keep = self.kw["keep"][1]
        return [{"role": "system", "content": "[step summary] 已完成旧轮次"}] + history[-keep:]


def _round_msgs():
    msgs = [{"role": "system", "content": "SYS"}]
    for i in range(3):  # 3 个旧轮次
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "function": {"name": "t", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"old{i}"})
    # 最近一轮
    msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "c9", "function": {"name": "t", "arguments": "{}"}}]})
    msgs.append({"role": "tool", "tool_call_id": "c9", "content": "recent"})
    return msgs


@pytest.mark.asyncio
async def test_step_summarize_replaces_old_rounds(monkeypatch):
    from _graphmod_support import build_agent
    import app.agent.graphmod.generate as gen_mod
    monkeypatch.setattr("app.middleware.summarization.HierarchicalSummarizationMiddleware", FakeSummarizer)
    monkeypatch.setattr(settings, "step_summary_keep_messages", 2)
    agent = build_agent()
    msgs = _round_msgs()
    out = await agent._step_summarize(msgs, budget=10_000)
    # 旧轮次被摘要替换，仅保留最近 keep 条 + 摘要
    contents = [m.get("content", "") for m in out]
    assert any("step summary" in str(c) for c in contents)
    assert any(str(c) == "recent" for c in contents)
    assert not any(str(c) == "old0" for c in contents)


@pytest.mark.asyncio
async def test_step_summarize_failure_keeps_raw(monkeypatch):
    from _graphmod_support import build_agent
    class Boom:
        def __init__(self, **kw):
            pass

        async def apply(self, history):
            raise RuntimeError("summarizer down")
    monkeypatch.setattr("app.middleware.summarization.HierarchicalSummarizationMiddleware", Boom)
    agent = build_agent()
    msgs = _round_msgs()
    assert await agent._step_summarize(msgs, 10_000) is msgs  # 回退原列表


@pytest.mark.asyncio
async def test_generate_long_task_small_step_wiring(monkeypatch, tmp_path):
    """长任务（≥min_rounds）周期性调用 _step_summarize，发送上下文保持有界。"""
    from _graphmod_support import FakeLLM, build_agent, make_state
    import app.agent.graphmod.generate as gen_mod
    monkeypatch.setattr(settings, "step_summary_enabled", True)
    monkeypatch.setattr(settings, "step_summary_min_rounds", 3)
    monkeypatch.setattr(settings, "step_summary_interval", 2)
    monkeypatch.setattr(settings, "step_summary_keep_messages", 4)
    monkeypatch.setattr(settings, "max_context_tokens", 10_000)
    monkeypatch.setattr(settings, "context_reserve_tokens", 1_000)
    monkeypatch.setattr(settings, "context_safety_ratio", 0.5)
    for n in ("record_model_call", "trace", "trace_messages"):
        monkeypatch.setattr(gen_mod, n, lambda *a, **k: None)

    agent = build_agent()
    monkeypatch.setattr(agent, "_build_tool_defs", lambda *a, **k: [])
    calls = []

    async def fake_sum(messages, budget):
        calls.append(len(messages))
        return [{"role": "system", "content": "SYS"},
                {"role": "system", "content": "[step summary] 已压缩"},
                messages[-1]]  # 只留摘要 + 最近一条
    monkeypatch.setattr(agent, "_step_summarize", fake_sum)

    llm = FakeLLM()
    llm.responses = ([FakeLLM().response(tool_calls=[("tool_probe", '{"a":"1"}')]) for _ in range(6)]
                     + [FakeLLM().response(content="done")])
    sent = []

    async def snap_llm(model, messages, tool_defs, state=None):
        sent.append([dict(m) for m in messages])
        return await llm(model, messages, tool_defs, state=state)
    agent._llm_call = snap_llm

    async def spy(name, args, state=None):
        return "小步数据" * 800  # 中文大输出
    agent._execute_tool = spy

    await agent._generate(make_state(_cwd=str(tmp_path)))
    # 第 4、6 轮触发摘要（>=3 且 %2==0）；第 7 轮收尾
    assert calls, "长任务应触发小步快走摘要"
    assert len(calls) >= 2
    # 摘要后所有发送快照估算 ≤ budget（4500）+ 摘要/系统余量
    for msgs in sent:
        est = tc.estimate_tokens_messages(msgs)
        assert est <= 4500 + 300, est