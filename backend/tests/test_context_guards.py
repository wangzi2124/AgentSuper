# -*- coding: utf-8 -*-
"""[C5] 上下文护栏 live 功能测试：硬护栏预算、自适应估算校正、工具输出 token 封顶、
STEP_STATE 落盘、小步快走摘要替换、tool_defs 前缀缓存冻结。

（该文件替代原 test_context_overrun.py 中 dead long-task/code_agent 之外的部分；
死类的多请求接力 LongTaskCoordinator 已随 code_agent/long_task 一并移除。）
覆盖：
  - budget.py：llm_call_budget（usable×safety_ratio）、compaction_target_tokens
  - token_counter.py：set/update_token_correction（EMA + 钳制）
  - core._llm_call：用实际 usage 自适应校准估算系数（流式 + 非流式回退）
  - generate.py：截断目标使用 llm_call_budget；多轮执行写 STEP_STATE；tool_defs 冻结
  - step_state.py：写/读 latest STEP_STATE
运行：pytest tests/test_context_guards.py
"""
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.config import settings
from app.context import budget
from app.context import step_state
from app.context import token_counter as tc


# ── budget ─────────────────────────────────────────────────────────────────

def test_llm_call_budget(monkeypatch):
    monkeypatch.setattr(settings, "max_context_tokens", 100_000)
    monkeypatch.setattr(settings, "context_reserve_tokens", 10_000)
    monkeypatch.setattr(settings, "context_safety_ratio", 0.9)
    assert budget.usable_context_tokens() == 90_000
    assert budget.llm_call_budget() == 81_000  # ×0.9 安全系数
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
    # 低估（实际 1.3× 估算）→ 系数上修
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


def test_estimate_scales_with_correction(monkeypatch):
    monkeypatch.setattr(tc, "_native_enabled", False)
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
    assert tc.token_correction_factor() > before  # 实际 150 > 估算 8 → 系数上修
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
    monkeypatch.setattr(settings, "step_summary_enabled", False)
    for n in ("record_model_call", "trace", "trace_messages"):
        monkeypatch.setattr(gen_mod, n, lambda *a, **k: None)

    agent = build_agent()
    monkeypatch.setattr(agent, "_build_tool_defs", lambda *a, **k: [])  # 省 schema 开销
    llm = FakeLLM()
    llm.responses = [
        FakeLLM().response(tool_calls=[("tool_write_file", '{"path": "/x/a.py"}')]),
        FakeLLM().response(tool_calls=[("tool_write_file", '{"path": "/x/a.py"}')]),
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
    # STEP_STATE 已落盘（第二轮执行后）且 files 从写工具实参提取（步骤交接用）
    step_files = list((tmp_path / ".agents" / "steps").glob("*.md"))
    assert any(p.name == "latest.md" for p in step_files)
    body = "".join(p.read_text(encoding="utf-8") for p in step_files if p.name != "latest.md")
    assert "/x/a.py" in body  # tool_write_file 的 path 已被提取进 STEP_STATE


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


# ── [C5 · G] tool_defs 前缀缓存冻结 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_freezes_tool_defs_for_cache(monkeypatch, tmp_path):
    """请求内 tool_defs 冻结：每轮传给 LLM 的 tools 参数字节一致（前缀缓存）。"""
    from _graphmod_support import FakeLLM, build_agent, make_state
    import app.agent.graphmod.generate as gen_mod
    monkeypatch.setattr(settings, "step_summary_enabled", False)
    for n in ("record_model_call", "trace", "trace_messages"):
        monkeypatch.setattr(gen_mod, n, lambda *a, **k: None)

    agent = build_agent()
    # 用真实 _build_tool_defs 包一层计数：每轮返回的必须是同一对象（冻结）
    orig = agent._build_tool_defs
    seen = []

    def frozen_build(question="", used=None, conversation_id="", model=""):
        d = orig(question, used, conversation_id, model)
        seen.append(d)
        return d
    monkeypatch.setattr(agent, "_build_tool_defs", frozen_build)

    llm = FakeLLM()
    llm.responses = [
        FakeLLM().response(tool_calls=[("tool_probe", '{"a":"1"}')]),
        FakeLLM().response(tool_calls=[("tool_probe", '{"a":"1"}')]),
        FakeLLM().response(content="done"),
    ]

    async def spy(name, args, state=None):
        return "R"
    agent._execute_tool = spy

    td_by_call = []

    async def snap_llm(model, messages, tool_defs, state=None):
        td_by_call.append(tool_defs)
        return await llm(model, messages, tool_defs, state=state)
    agent._llm_call = snap_llm

    await agent._generate(make_state(_cwd=str(tmp_path), model="deepseek/deepseek-v4-flash"))
    # 冻结：只有入口构建一次，循环不再重挂载、每轮 tools 参数为同一对象
    assert len(seen) == 1, f"tool_defs 应只构建一次，实际 {len(seen)} 次"
    assert all(td is td_by_call[0] for td in td_by_call[1:])