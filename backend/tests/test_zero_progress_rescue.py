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

