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


# ── 方案 E/F · 长任务多请求接力 ─────────────────────────────────────────────

def test_parse_plan():
    from app.agent.long_task import _parse_plan
    assert _parse_plan('["a", "b", "c"]', 5) == ["a", "b", "c"]
    assert _parse_plan('```json\n["x"]\n```', 5) == ["x"]
    assert _parse_plan("not json", 5) == []
    assert _parse_plan('["a", "b", "c", "d"]', 3) == ["a", "b", "c"]  # 超限截断
    assert _parse_plan("", 5) == []


class _CoordAgent:
    """假 RAGAgent：_llm_call 返回计划；invoke 记录每步上下文。"""

    def __init__(self, plan_text, answers):
        self.plan_text = plan_text
        self.answers = answers
        self.model = "m"
        self.invoke_calls = []

    async def _llm_call(self, model, messages, tool_defs, state=None):
        from types import SimpleNamespace
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=self.plan_text))])

    async def invoke(self, question, use_vector_db=False, directory="", conversation_id=""):
        self.invoke_calls.append((question, directory, conversation_id))
        return {"answer": self.answers[len(self.invoke_calls) - 1],
                "sources": [], "steps": [], "tokens": {"input": 10, "output": 5}}


@pytest.mark.asyncio
async def test_coordinator_single_step_falls_back(monkeypatch):
    from app.agent.long_task import LongTaskCoordinator
    agent = _CoordAgent('["single"]', ["普通回答"])
    out = await LongTaskCoordinator(agent, max_steps=6).run("q")
    assert out["answer"] == "普通回答"
    assert len(agent.invoke_calls) == 1  # 非长任务 → 普通单请求


@pytest.mark.asyncio
async def test_coordinator_multi_step_fresh_context(tmp_path):
    from app.agent.long_task import LongTaskCoordinator
    agent = _CoordAgent('["步骤1", "步骤2", "步骤3"]', ["A1", "A2", "A3"])
    out = await LongTaskCoordinator(agent, max_steps=6).run("长任务", directory=str(tmp_path), conversation_id="cid")
    assert len(agent.invoke_calls) == 3  # 每步一个独立请求
    # 每步上下文只含计划 + 已完成进度 + 当前步（fresh，不携带旧步骤全文）
    for i, (q, d, cid) in enumerate(agent.invoke_calls):
        assert "长任务" in q
        assert f"当前第 {i+1}/3 步" in q
        assert "已完成进度" in q
        assert d == str(tmp_path) and cid == "cid"
    # 汇总回答含各步结果
    assert "【步骤1】A1" in out["answer"] and "【步骤3】A3" in out["answer"]
    assert out["tokens"]["input"] == 30  # 3 步 × 10
    # STEP_STATE 落盘（协调器 seq 偏移 1000+，不与 in-loop 冲突）
    latest = list((tmp_path / ".agents" / "steps").glob("1003.md"))
    assert latest and "step3" in latest[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_coordinator_file_handoff(tmp_path):
    """步骤文件交接：前一步产出的文件注入下一步 prompt（fresh context 信息桥）。"""
    from app.agent.long_task import LongTaskCoordinator
    agent = _CoordAgent('["步骤1", "步骤2"]', [
        "完成了模块，产出文件: calculator.py, test_calculator.py",
        "读取了已有文件继续",
    ])
    out = await LongTaskCoordinator(agent, max_steps=6).run("建项目", directory=str(tmp_path))
    # 第 2 步 prompt 注入了第 1 步产出的文件
    q2 = agent.invoke_calls[1][0]
    assert "calculator.py" in q2 and "test_calculator.py" in q2
    assert "已完成步骤产出的文件" in q2
    # 协调器 STEP_STATE 的 files 携带产出文件（resume 可读回）
    coord_files = list((tmp_path / ".agents" / "steps").glob("1002.md"))
    assert coord_files and "calculator.py" in coord_files[0].read_text(encoding="utf-8")
    assert out["answer"]


def test_parse_files_from_answer():
    from app.agent.long_task import _parse_files_from_answer
    assert _parse_files_from_answer("完成。产出文件: a.py, b/c.py") == ["a.py", "b/c.py"]
    assert _parse_files_from_answer("完成。产出文件：x.py") == ["x.py"]
    assert _parse_files_from_answer("无产出文件") == []


@pytest.mark.asyncio
async def test_code_agent_wires_coordinator_when_enabled(monkeypatch):
    """LONG_TASK_STEP_MODE=true 且问题够长时 code 子 Agent chat 走接力。"""
    from app.agent.code_agent import CodeAgent
    from app.agent.base import AgentMessage
    from _graphmod_support import build_agent
    monkeypatch.setattr(settings, "long_task_step_mode", True)
    monkeypatch.setattr(settings, "long_task_min_question_chars", 5)

    inner = build_agent()
    coordinator_calls = []

    async def fake_run(self, question, directory="", conversation_id=""):
        coordinator_calls.append((question, directory, conversation_id))
        return {"answer": "RELAY", "sources": [], "steps": [], "tokens": {}}
    monkeypatch.setattr("app.agent.long_task.LongTaskCoordinator.run", fake_run)

    agent = CodeAgent(inner=inner)
    msg = AgentMessage(source="user", target="code", type="request", action="chat",
                       payload={"question": "写一个多文件项目并运行测试", "conversation_id": "cid", "directory": "/w"})
    replies = [r async for r in agent.handle_message(msg)]
    assert replies[0].payload["answer"] == "RELAY"
    assert coordinator_calls == [("写一个多文件项目并运行测试", "/w", "cid")]


@pytest.mark.asyncio
async def test_code_agent_short_question_skips_planning(monkeypatch):
    """门控：问题过短即使 step_mode 开启也不走接力（零规划开销）。"""
    from app.agent.code_agent import CodeAgent
    from app.agent.base import AgentMessage
    monkeypatch.setattr(settings, "long_task_step_mode", True)
    monkeypatch.setattr(settings, "long_task_min_question_chars", 30)
    called = []
    coordinator_calls = []

    class Inner:
        async def invoke(self, **kw):
            called.append(kw.get("question"))
            return {"answer": "NORMAL", "sources": [], "steps": [], "tokens": {}}
    monkeypatch.setattr("app.agent.long_task.LongTaskCoordinator.run",
                        lambda self, question, directory="", conversation_id="": coordinator_calls.append(question) or {})
    agent = CodeAgent(inner=Inner())
    msg = AgentMessage(source="user", target="code", type="request", action="chat",
                       payload={"question": "你好"})  # 2 字符 < 30
    replies = [r async for r in agent.handle_message(msg)]
    assert replies[0].payload["answer"] == "NORMAL"
    assert called == ["你好"]
    assert coordinator_calls == []  # 未触发规划


@pytest.mark.asyncio
async def test_code_agent_normal_when_disabled(monkeypatch):
    from app.agent.code_agent import CodeAgent
    from app.agent.base import AgentMessage
    monkeypatch.setattr(settings, "long_task_step_mode", False)
    called = []

    class Inner:
        async def invoke(self, **kw):
            called.append(kw.get("question"))
            return {"answer": "NORMAL", "sources": [], "steps": [], "tokens": {}}
    agent = CodeAgent(inner=Inner())
    msg = AgentMessage(source="user", target="code", type="request", action="chat",
                       payload={"question": "简单问题"})
    replies = [r async for r in agent.handle_message(msg)]
    assert replies[0].payload["answer"] == "NORMAL"
    assert called == ["简单问题"]


@pytest.mark.asyncio
async def test_generate_freezes_tool_defs_for_cache(monkeypatch, tmp_path):
    """[C5 · G] 请求内 tool_defs 冻结：每轮传给 LLM 的 tools 参数字节一致（前缀缓存）。"""
    from _graphmod_support import FakeLLM, build_agent, make_state
    import app.agent.graphmod.generate as gen_mod
    monkeypatch.setattr(settings, "step_summary_enabled", False)
    for n in ("record_model_call", "trace", "trace_messages"):
        monkeypatch.setattr(gen_mod, n, lambda *a, **k: None)

    agent = build_agent()
    # 用真实 _build_tool_defs 包一层计数：每轮返回的必须是同一对象（冻结）
    orig = agent._build_tool_defs
    seen = []

    def frozen_build(question="", used=None):
        d = orig(question, used)
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

    await agent._generate(make_state(_cwd=str(tmp_path)))
    # 冻结：只有入口构建一次，循环不再重挂载 → 每轮 tools 参数为同一对象
    assert len(seen) == 1, f"tool_defs 应只构建一次，实际 {len(seen)} 次"
    assert all(td is td_by_call[0] for td in td_by_call[1:])


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