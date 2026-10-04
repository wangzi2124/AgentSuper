"""零进展救援（reasoning 预算耗尽 → 强制产出）的回归测试。

背景：deepseek-v4-flash 在「在 D:/game 写 React 俄罗斯方块」任务上把单轮 8192 输出
token 全部花在 reasoning_content 上，content 空 + tool_calls 空 + finish_reason=length，
循环直接退出 → 用户收到 2.7 万字内心独白而磁盘零文件。

这些用例不依赖真实模型：用假 msg/response 驱动 `_generate` 的控制流断言
「续跑次数、是否回灌独白、是否保留工具」。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── 1. _is_zero_progress：区分「真截断回答」与「思考耗尽的独白」 ────────────

def test_empty_content_is_zero_progress():
    from app.agent.graphmod.generate import RAGAgentGenerate
    msg = types.SimpleNamespace(content="", tool_calls=None)
    assert RAGAgentGenerate._is_zero_progress(msg) is True


def test_tool_calls_is_not_zero_progress():
    from app.agent.graphmod.generate import RAGAgentGenerate
    tc = types.SimpleNamespace(id="1", function=types.SimpleNamespace(name="t", arguments="{}"))
    msg = types.SimpleNamespace(content="", tool_calls=[tc])
    assert RAGAgentGenerate._is_zero_progress(msg) is False


def test_real_content_is_not_zero_progress():
    from app.agent.graphmod.generate import RAGAgentGenerate
    msg = types.SimpleNamespace(content="已完成：写入 3 个文件。", tool_calls=None)
    assert RAGAgentGenerate._is_zero_progress(msg) is False


def test_reasoning_fallback_content_counts_as_zero_progress():
    """核心回归：core 把 reasoning_content 回退填进 content 后，
    绝不能被当成「有回答」（否则 2.7 万字独白伪装成成功）。"""
    from app.agent.graphmod.generate import RAGAgentGenerate
    msg = types.SimpleNamespace(
        content="Let me plan the implementation. " * 500,
        tool_calls=None,
        _content_from_reasoning=True,
    )
    assert RAGAgentGenerate._is_zero_progress(msg) is True


# ── 2. 常量：催促提示必须要求「动手」而非继续推演 ──────────────────────────

def test_zero_progress_prompt_forces_action():
    from app.agent.graphmod.constants import ZERO_PROGRESS_PROMPT, ZERO_PROGRESS_ACK
    assert "tool_write_file" in ZERO_PROGRESS_PROMPT
    assert "不要再做方案推演" in ZERO_PROGRESS_PROMPT
    # ACK 必须短：不能把上轮独白塞回上下文
    assert len(ZERO_PROGRESS_ACK) < 80


# ── 3. 配置默认值 ─────────────────────────────────────────────────────────

def test_rescue_attempts_default_is_one():
    from app.config import Settings
    s = Settings(_env_file=None)
    assert s.zero_progress_rescue_attempts == 1
    assert s.zero_progress_mask_reasoning is True


# ── 4. 独白遮蔽文案 ──────────────────────────────────────────────────────

def test_mask_reasoning_message_is_diagnostic_not_monologue():
    """最终失败时返回的应是诊断文案，且不含独白内容。"""
    from app.agent.graphmod.generate import RAGAgentGenerate
    monologue = "SECRET_MONOLOGUE_MARKER " * 100
    msg = types.SimpleNamespace(
        content=monologue, tool_calls=None, _content_from_reasoning=True
    )
    assert RAGAgentGenerate._is_zero_progress(msg) is True
    # 遮蔽逻辑在 _generate 内联，这里只验证输入可被正确识别
    assert "SECRET_MONOLOGUE_MARKER" in msg.content


# ── 5. 循环不会因救援永久撑开 ────────────────────────────────────────────

def test_rescue_budget_is_bounded():
    """救援次数受 zero_progress_rescue_attempts 约束，设 0 即完全关闭该行为。"""
    from app.config import Settings
    s = Settings(_env_file=None, zero_progress_rescue_attempts=0)
    assert max(0, int(s.zero_progress_rescue_attempts)) == 0


# ── 4b. 末轮空响应不得覆盖已完成的工作 ──────────────────────────────────
# 真实事故（2026-10-03，session ses_208egtw0xz58cf43d）：用户要求在 D 盘写
# React 俄罗斯方块。Agent 连续 8 轮工具调用成功写入 5 个文件（engine.js /
# tetrisReducer.js / useTetris.js / tetrominoes.js / main.jsx，均 node --check
# 通过），末轮只吐 346 token 思考后 finish=stop 空响应。此前零进展掩码把整轮
# 成果替换成「本次请求未能产出任何内容 / 未执行任何工具」——用户看不到任何成果，
# 磁盘上的文件也不知道存在（文案还与事实相反）。

def test_mask_does_not_claim_no_tools_when_tools_ran():
    """有工具调用过时，step_end 不得写「未执行任何工具」。"""
    src = _generate_source()
    # 零进展播报必须按 used_tools 分流
    assert "if used_tools else" in src
    assert "全程未执行任何工具" in src
    # 曾经那句无条件断言「未执行任何工具」必须已消失
    assert 'cause={_cause}，未执行任何工具' not in src


def test_partial_completion_broadcast_lists_work():
    """干了活时改用「部分完成」播报，并如实列出工具与已落盘文件。"""
    src = _generate_source()
    assert "本次任务已执行部分工作，但最后一轮收尾失败" in src
    # 必须列出工具与文件，而不是宣称什么都没做
    assert "本轮已调用工具" in src
    assert "本轮写入的文件" in src
    assert "已落盘的改动仍然有效" in src


def test_round_files_registered_for_file_tools():
    """文件类工具的路径必须在执行处登记，供末轮空响应播报使用。"""
    src = _generate_source()
    assert "_round_files: set[str] = set()" in src
    # 两处执行路径（主循环 + 强制收尾轮）都要登记
    assert src.count("_round_files.update(") >= 2


def test_zero_progress_reasoning_size_threshold():
    """思考规模门槛：低于 2000 token 不算「预算耗尽」（实测末轮 346 token 形态）。"""
    from app.agent.graphmod.generate import (
        _ZERO_PROGRESS_REASONING_MIN_TOKENS,
        _zero_progress_cause,
    )
    assert _ZERO_PROGRESS_REASONING_MIN_TOKENS == 2000
    # 真实事故形态：末轮 346 token 思考、预算几乎没用
    assert _zero_progress_cause(
        from_reasoning=True, finish_reason="stop",
        output_tokens=16645, reasoning_tokens=9605,
    ) == "stop_without_output"
    # 真耗尽仍然成立
    assert _zero_progress_cause(
        from_reasoning=True, finish_reason="stop",
        output_tokens=8670, reasoning_tokens=8500,
    ) == "reasoning_budget_exhausted"


# ── 6. 回归：救援轮 tool_calls=None 不得崩溃 ─────────────────────────────
# 实测事故：救援轮 litellm 返回 tool_calls=None，循环体内 `for tc in msg.tool_calls`
# 直接抛 TypeError: 'NoneType' object is not iterable（generate.py:417），
# 整轮请求 500。统一出口 cur_tool_calls = list(... or []) 必须兜住。

def test_cur_tool_calls_normalizes_none():
    for raw in (None, [], ()):
        assert list(raw or []) == []


def test_rescue_round_with_none_tool_calls_is_iterable():
    """模拟救援轮：msg.tool_calls 为 None 时，下游遍历必须安全。"""
    msg = types.SimpleNamespace(content="", tool_calls=None)
    cur = list(getattr(msg, "tool_calls", None) or [])
    # 这些是循环体内的真实用法，救援轮下都不得抛
    assert [tc.function.name for tc in cur] == []
    assert [f"{tc.function.name}:{tc.function.arguments}" for tc in cur] == []
    # 循环体内的 task_only 判定（救援轮必为 False）
    task_only = bool(cur) and all(tc.function.name == "tool_task" for tc in cur)
    assert task_only is False


def _generate_source() -> str:
    """generate.py 全文，供源码级静态守卫使用。"""
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "app/agent/graphmod/generate.py").read_text(
        encoding="utf-8")


def test_generate_source_has_no_bare_iteration_of_msg_tool_calls():
    """静态守卫：循环体内不得再出现裸 `for ... in msg.tool_calls`。

    守卫范围是 while 循环体（cur_tool_calls 定义之后 → 循环外 max-rounds 分支之前），
    那里必须统一走 cur_tool_calls。
    """
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/agent/graphmod/generate.py").read_text(
        encoding="utf-8")
    start = src.index("cur_tool_calls = list(")
    end = src.index('if msg.tool_calls:', start)
    body = src[start:end]
    bare = re.findall(r"for\s+\w+\s+in\s+msg\.tool_calls", body)
    assert not bare, f"循环体内仍有裸遍历 msg.tool_calls: {bare}"


def test_generate_source_has_no_bare_len_of_msg_tool_calls():
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/agent/graphmod/generate.py").read_text(
        encoding="utf-8")
    start = src.index("cur_tool_calls = list(")
    end = src.index('if msg.tool_calls:', start)
    body = src[start:end]
    assert not re.findall(r"len\(msg\.tool_calls\)", body)


# ── 7. 超长内联正文救援（无视「长内容写文件」规则 → 预算烧在聊天窗口）────────
# 实测事故（2026-10-03，同一模型 / 同一「在 D 盘写个俄罗斯方块游戏」任务）：
# 前几轮正常 ls/read_file，第 4 轮把 8192 输出预算全写成 24,258 字符的聊天正文，
# tool_calls=[] + finish_reason=length。`_is_zero_progress` 因 content 非空判 False
# → 救援不触发 → while 因无 tool_calls 退出 → 磁盘零文件。
# 这是与「零进展」互补的另一档：做了，但做在聊天里而非磁盘上。

def test_oversized_prose_detected_when_truncated_without_tools():
    from app.agent.graphmod.generate import RAGAgentGenerate
    msg = types.SimpleNamespace(content="超长正文" * 20000, tool_calls=None)
    assert RAGAgentGenerate._is_oversized_prose(msg, "length") is True
    # 关键：这一档绝不能被误判成「零进展」，否则提示词话术会说「没有产出任何正文」（假的）
    assert RAGAgentGenerate._is_zero_progress(msg) is False


def test_oversized_prose_not_triggered_when_tools_called():
    """带工具调用时不算「内联正文」—— 那是在正常干活（哪怕同时被截断）。"""
    from app.agent.graphmod.generate import RAGAgentGenerate
    tc = types.SimpleNamespace(id="1", function=types.SimpleNamespace(name="tool_write_file", arguments="{}"))
    msg = types.SimpleNamespace(content="正文" * 5000, tool_calls=[tc])
    assert RAGAgentGenerate._is_oversized_prose(msg, "length") is False


def test_oversized_prose_requires_length_finish_reason():
    """非 length 收尾（正常 stop）不得触发，避免对正常长回答多问一轮。"""
    from app.agent.graphmod.generate import RAGAgentGenerate
    msg = types.SimpleNamespace(content="正文" * 5000, tool_calls=None)
    assert RAGAgentGenerate._is_oversized_prose(msg, "stop") is False
    assert RAGAgentGenerate._is_oversized_prose(msg, "tool-calls") is False


def test_oversized_prose_below_threshold_ignored():
    """短回答恰好被截属于偶发，不触发救援（避免对短问答多问一轮）。"""
    from app.agent.graphmod.constants import OVERSIZED_PROSE_MIN_CHARS
    from app.agent.graphmod.generate import RAGAgentGenerate
    msg = types.SimpleNamespace(content="x" * (OVERSIZED_PROSE_MIN_CHARS - 1), tool_calls=None)
    assert RAGAgentGenerate._is_oversized_prose(msg, "length") is False


def test_oversized_prose_prompt_forces_file_writes():
    from app.agent.graphmod.constants import (
        OVERSIZED_PROSE_ACK, OVERSIZED_PROSE_PROMPT,
    )
    # 必须点名写文件工具，且给出「分段写」的具体做法
    assert "tool_write_file" in OVERSIZED_PROSE_PROMPT
    assert "tool_append_file" in OVERSIZED_PROSE_PROMPT
    # 话术不能沿用零进展那套「没有产出任何正文」——本档正文非空，说假话会削弱模型信任
    assert "没有产出任何正文" not in OVERSIZED_PROSE_PROMPT
    assert "截断" in OVERSIZED_PROSE_PROMPT
    # ACK 必须短：绝不能把上轮 2 万多字正文塞回上下文（那正是吃光预算的东西）
    assert len(OVERSIZED_PROSE_ACK) < 80
    assert "tool_write_file" not in OVERSIZED_PROSE_ACK


def test_generate_injects_oversized_prose_nudge_and_continues(monkeypatch):
    """控制流回归：超长内联正文被截断时，循环必须**续跑**并注入落盘提示。

    断言两件事：
      1) 第 2 次 LLM 调用真的发生了（没有静默收尾）；
      2) 该轮上下文里带上了 OVERSIZED_PROSE_PROMPT，且**没有**回灌那 24K 字正文。
    """
    import asyncio
    import json as _json

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _graphmod_support import FakeLLM, build_agent, make_state
    from app.agent.graphmod.constants import OVERSIZED_PROSE_PROMPT
    from app.config import settings
    import app.agent.graphmod.generate as gen_mod

    monkeypatch.setattr(settings, "max_context_tokens", 10_000)
    monkeypatch.setattr(settings, "context_reserve_tokens", 1_000)
    monkeypatch.setattr(settings, "context_safety_ratio", 0.5)
    monkeypatch.setattr(settings, "compaction_threshold_tokens", 0)
    monkeypatch.setattr(settings, "zero_progress_rescue_attempts", 1)
    monkeypatch.setattr(settings, "step_summary_enabled", False)
    for n in ("record_model_call", "trace", "trace_messages"):
        monkeypatch.setattr(gen_mod, n, lambda *a, **k: None)

    agent = build_agent()
    monkeypatch.setattr(agent, "_build_tool_defs", lambda *a, **k: [])

    huge = "超长正文" * 20000
    llm = FakeLLM()
    llm.responses = [
        FakeLLM().response(content=huge, finish_reason="length"),
        FakeLLM().response(content="done"),
    ]
    seen = []

    async def spy_llm(model, messages, tool_defs, state=None):
        seen.append([dict(m) for m in messages])
        return await llm(model, messages, tool_defs, state=state)

    agent._llm_call = spy_llm

    async def noop_tool(name, args, state=None):
        return "工具输出"

    agent._execute_tool = noop_tool

    out = asyncio.run(agent._generate(make_state(question="在 D 盘写个俄罗斯方块游戏")))

    # 1) 必须续跑：至少 2 次 LLM 调用
    assert len(seen) >= 2, f"未续跑，只调用了 {len(seen)} 次: {seen}"
    # 2) 第二次调用带上了落盘提示，且没回灌 2 万多字正文
    #    （直接拼 content比较：json.dumps 会把换行转义成 \n，多行提示词匹配不上）
    blob = "\n".join(str(m.get("content") or "") for m in seen[1])
    assert OVERSIZED_PROSE_PROMPT in blob, "未注入超长正文救援提示"
    assert "超长正文" not in blob, "把上轮 24K 字正文回灌进了上下文"
    assert out["answer"] == "done"


def test_generate_shortcut_ignores_reasoning_fallback_content(monkeypatch):
    """核心回归：救援轮不得因「reasoning 回退正文非空」而跳过重试。

    实测事故（2026-10-03，「在 D 盘写个俄罗斯方块游戏」）：
      第 4 轮 content 空、reasoning 23,844 字符、finish=length、tool_calls=[]，
      `core._llm_call` 把 reasoning 回退填进 content 并打 `_content_from_reasoning`。
      `_is_zero_progress` 正确判 True → 救援注入催促 → 但「已拿到正文就收尾」捷径
      被那份独白满足 → 当场 break → 模型从未拿到那次重试 → 磁盘零文件，
      用户只收到 291 字诊断文案。救援对思考模型一直是空转。
    """
    import asyncio

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _graphmod_support import FakeLLM, build_agent, make_state
    from app.agent.graphmod.constants import ZERO_PROGRESS_PROMPT
    from app.config import settings
    import app.agent.graphmod.generate as gen_mod

    monkeypatch.setattr(settings, "max_context_tokens", 10_000)
    monkeypatch.setattr(settings, "context_reserve_tokens", 1_000)
    monkeypatch.setattr(settings, "context_safety_ratio", 0.5)
    monkeypatch.setattr(settings, "compaction_threshold_tokens", 0)
    monkeypatch.setattr(settings, "zero_progress_rescue_attempts", 1)
    monkeypatch.setattr(settings, "step_summary_enabled", False)
    for n in ("record_model_call", "trace", "trace_messages"):
        monkeypatch.setattr(gen_mod, n, lambda *a, **k: None)

    agent = build_agent()
    monkeypatch.setattr(agent, "_build_tool_defs", lambda *a, **k: [])

    monologue = "内心独白 " * 6000
    llm = FakeLLM()
    zero = FakeLLM().response(content="", finish_reason="length")
    # 模拟 core 的 reasoning 回退：content 为空但独白被填进去并打标记
    zero.choices[0].message.content = monologue
    zero.choices[0].message.reasoning_content = monologue
    zero.choices[0].message._content_from_reasoning = True
    llm.responses = [zero, FakeLLM().response(content="done")]

    seen = []

    async def spy_llm(model, messages, tool_defs, state=None):
        seen.append([dict(m) for m in messages])
        return await llm(model, messages, tool_defs, state=state)

    agent._llm_call = spy_llm

    async def noop_tool(name, args, state=None):
        return "工具输出"

    agent._execute_tool = noop_tool

    out = asyncio.run(agent._generate(make_state(question="在 D 盘写个俄罗斯方块游戏")))

    assert len(seen) >= 2, f"救援被独白骗过，没有重试（只调用 {len(seen)} 次）"
    blob = "\n".join(str(m.get("content") or "") for m in seen[1])
    assert ZERO_PROGRESS_PROMPT in blob, "未注入零进展催促提示"
    assert monologue not in blob, "把 2.4 万字内心独白回灌进了上下文"
    assert out["answer"] == "done"


def test_zero_progress_rescue_ignores_finish_reason():
    """回归：思考模型烧光预算后 Provider 可能报 `stop` 而非 `length`。

    实测（2026-10-03 变体）：content 空、reasoning 10,219 字符、finish_reason=stop。
    若用 length 门禁，救援不触发 → 只返回 291 字诊断文案 → 磁盘零文件。
    零进展档的 content 按定义不是真答案，所以不该受 finish_reason 限制。
    """
    import asyncio

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _graphmod_support import FakeLLM, build_agent, make_state
    from app.agent.graphmod.constants import ZERO_PROGRESS_PROMPT
    from app.config import settings
    import app.agent.graphmod.generate as gen_mod

    monkey = pytest.MonkeyPatch()
    monkey.setattr(settings, "max_context_tokens", 10_000)
    monkey.setattr(settings, "context_reserve_tokens", 1_000)
    monkey.setattr(settings, "context_safety_ratio", 0.5)
    monkey.setattr(settings, "compaction_threshold_tokens", 0)
    monkey.setattr(settings, "zero_progress_rescue_attempts", 1)
    monkey.setattr(settings, "step_summary_enabled", False)
    for n in ("record_model_call", "trace", "trace_messages"):
        monkey.setattr(gen_mod, n, lambda *a, **k: None)
    try:
        agent = build_agent()
        monkey.setattr(agent, "_build_tool_defs", lambda *a, **k: [])

        monologue = "内心独白 " * 3000
        stop = FakeLLM().response(content="", finish_reason="stop")
        stop.choices[0].message.content = monologue
        stop.choices[0].message.reasoning_content = monologue
        stop.choices[0].message._content_from_reasoning = True

        llm = FakeLLM()
        llm.responses = [stop, FakeLLM().response(content="done")]
        seen = []

        async def spy_llm(model, messages, tool_defs, state=None):
            seen.append([dict(m) for m in messages])
            return await llm(model, messages, tool_defs, state=state)

        agent._llm_call = spy_llm

        async def noop_tool(name, args, state=None):
            return "工具输出"

        agent._execute_tool = noop_tool

        out = asyncio.run(agent._generate(make_state(question="在 D 盘写个俄罗斯方块游戏")))
    finally:
        monkey.undo()

    assert len(seen) >= 2, f"finish=stop 的零进展未被救援（只调用 {len(seen)} 次）"
    blob = "\n".join(str(m.get("content") or "") for m in seen[1])
    assert ZERO_PROGRESS_PROMPT in blob, "未注入零进展催促提示"
    assert out["answer"] == "done"


def test_plain_empty_answer_still_goes_to_empty_answer_retry():
    """反向守卫：不带 `_content_from_reasoning` 的空回答不得被零进展救援抢走。

    空回答归既有的 EMPTY_ANSWER 重试 / 弱模型强模型兜底路径管（轮次由
    `test_graphmod_generate_core.py::test_generate_empty_content_default` 锁定）；
    抢过来会多烧一轮并挤掉强模型兜底。
    """
    import asyncio

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _graphmod_support import FakeLLM, build_agent, make_state
    from app.config import settings
    import app.agent.graphmod.generate as gen_mod

    monkey = pytest.MonkeyPatch()
    monkey.setattr(settings, "max_context_tokens", 10_000)
    monkey.setattr(settings, "context_reserve_tokens", 1_000)
    monkey.setattr(settings, "context_safety_ratio", 0.5)
    monkey.setattr(settings, "compaction_threshold_tokens", 0)
    monkey.setattr(settings, "zero_progress_rescue_attempts", 1)
    monkey.setattr(settings, "empty_answer_retry", 1)
    monkey.setattr(settings, "weak_model_strong_fallback", False)
    monkey.setattr(settings, "step_summary_enabled", False)
    for n in ("record_model_call", "trace", "trace_messages"):
        monkey.setattr(gen_mod, n, lambda *a, **k: None)
    try:
        agent = build_agent()
        monkey.setattr(agent, "_build_tool_defs", lambda *a, **k: [])

        llm = FakeLLM()
        llm.responses = [FakeLLM().response(content=""), FakeLLM().response(content="ok")]
        seen = []

        async def spy_llm(model, messages, tool_defs, state=None):
            seen.append([dict(m) for m in messages])
            return await llm(model, messages, tool_defs, state=state)

        agent._llm_call = spy_llm

        async def noop_tool(name, args, state=None):
            return "工具输出"

        agent._execute_tool = noop_tool

        out = asyncio.run(agent._generate(make_state(question="你好")))
    finally:
        monkey.undo()

    assert out["answer"] == "ok"
    for batch in seen[1:]:
        blob = "\n".join(str(m.get("content") or "") for m in batch)
        assert "没有产出任何正文" not in blob, "空回答被误判为零进展救援"


def test_oversized_prose_still_requires_length_finish_reason():
    """反向守卫：超长正文档仍必须靠 length 证明「非空正文是残稿」，不得放宽。"""
    from app.agent.graphmod.generate import RAGAgentGenerate
    msg = types.SimpleNamespace(content="正文" * 5000, tool_calls=None)
    assert RAGAgentGenerate._is_oversized_prose(msg, "stop") is False


def test_generate_shortcut_only_for_zero_progress_kind():
    """静态守卫：「已拿到正文就收尾」捷径必须限定零进展档。

    该判断点处`msg` 仍是**触发救援那一轮**的响应。超长内联正文档正文非空，
    若不限定档位就会当场 break —— 救援空转，模型永远学不会落盘（实测踩过）。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/agent/graphmod/generate.py").read_text(
        encoding="utf-8")
    assert 'rescue_kind == "zero_progress"' in src, "收尾捷径未限定档位"
    assert "rescue_kind: str | None = None" in src, "rescue_kind 未初始化"


def test_generate_source_gates_oversized_prose_on_not_zero_progress():
    """静态守卫：两档判据必须在 `_rescue_kind` 里**串行判**且顺序为先零进展。

    `_is_oversized_prose` 单独用会与零进展重叠（空正文 + length），话术会说
    「没有产出任何正文」；零进展单独用又漏掉本档。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/agent/graphmod/generate.py").read_text(
        encoding="utf-8")
    assert "def _rescue_kind(" in src, "缺少 _rescue_kind 分档函数"
    body = src[src.index("def _rescue_kind("):src.index("def _maybe_rescue(")]
    assert body.index("self._is_zero_progress(m)") < body.index(
        "self._is_oversized_prose(m, fr)"), "零进展必须先判，避免话术说假话"
    assert "OVERSIZED_PROSE_PROMPT" in src, "超长正文提示未接入"


def test_generate_checks_rescue_before_entering_loop():
    """静态守卫：救援判定必须在 `while` 之前也跑一次。

    `while` 条件只看 `tool_calls / tool-calls / rescue_pending`：若首轮响应就是
    超长内联正文，循环压根不会进入，只挂在循环末尾的救援就是死代码。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/agent/graphmod/generate.py").read_text(
        encoding="utf-8")
    pre_loop = src[:src.index("while (")]
    assert "_maybe_rescue(msg, finish_reason, 0)" in pre_loop, (
        "首轮响应未纳入救援判定，超长正文/零进展在第一轮会静默失效")
    # 循环内也仍需保留一次（后续轮次同样要判）
    loop_body = src[src.index("while ("):]
    assert "_maybe_rescue(msg, finish_reason, rounds)" in loop_body, "循环内救援判定被移除"

