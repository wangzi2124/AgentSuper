"""零进展失败的可观测性回归测试。

背景（实测）：`deepseek-v4-flash` 声明 `reasoning: true`，输出预算
`limits.max_output_tokens = 8192` 由 reasoning 与 content **共享**。
「D:\\game 写 React 俄罗斯方块」任务下第二轮 LLM 调用出现
`ct == reasoning == 8192` + `finish_reason == length` + 零 tool call →
工具循环根本没进入 → 目录零文件；用户反而收到 27908 字内心独白
（`core.py` 的 reasoning_content 回退把「零产出」伪装成了「正常回答」）。

本测试锁定两点：
1. `_chainlog_llm` 会把这种调用标成 `no_progress` 并升级为 ERROR；
2. `_generate` 收尾时会额外记一条 `generate.zero_progress` 节点，
   且 `step_end` 的 status 为 error（不能报「完成」骗用户）。
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.graphmod import core as core_mod  # noqa: E402
from app.agent.graphmod import generate as gen_mod  # noqa: E402


# ── 1. _chainlog_llm 的零进展标记 ─────────────────────────────────────────

def _capture_llm_log(monkeypatch, **kwargs):
    """跑一次 _chainlog_llm，返回写入 chainlog 的 (level, stage, event, data)。"""
    got = {}

    class _FakeChainlog:
        @staticmethod
        def log(level, stage, component, event, **kw):
            got.update(level=level, stage=stage, component=component, event=event, **kw)

    class _FakeSettings:
        chain_log_enabled = True
        chain_log_llm_calls = True

    import app
    import app.config as cfg

    # `from app import chainlog` 走的是 **包属性** 查找，不是 sys.modules，
    # 所以必须 setattr 到 app 上（只 patch sys.modules 不生效）
    monkeypatch.setattr(app, "chainlog", _FakeChainlog, raising=False)
    monkeypatch.setattr(cfg, "settings", _FakeSettings, raising=False)
    core_mod._chainlog_llm("deepseek/deepseek-v4-flash", **kwargs)
    return got


def test_llm_log_flags_reasoning_budget_exhaustion(monkeypatch):
    """思考吃掉全部输出预算 + 零 tool call → ERROR + no_progress"""
    got = _capture_llm_log(
        monkeypatch,
        where="invoke", duration_ms=31040.9, state=None,
        prompt_tokens=7252, completion_tokens=8192, reasoning_tokens=8192,
        cache_read=7040, cache_write=212, cost=0.0095,
        finish_reason="length", tool_calls=0, has_content=False,
    )
    assert got["level"] == "ERROR"
    assert got["data"]["no_progress"] is True
    assert got["data"]["no_progress_reason"] == "budget_exhausted_by_reasoning"
    assert got["data"]["reasoning_ratio"] == 1.0
    assert "零进展" in got["message"]


def test_llm_log_flags_length_without_tool_calls(monkeypatch):
    """length + 无工具调用（即便 reasoning 未占满）也是零进展"""
    got = _capture_llm_log(
        monkeypatch,
        where="invoke", duration_ms=100.0, state=None,
        prompt_tokens=100, completion_tokens=500, reasoning_tokens=10,
        finish_reason="length", tool_calls=0, has_content=False,
    )
    assert got["data"]["no_progress"] is True
    assert got["data"]["no_progress_reason"] == "length_without_tool_calls"
    # reasoning 未占满时不能误报 ratio=1
    assert got["data"]["reasoning_ratio"] == 0.02


def test_llm_log_normal_tool_call_not_flagged(monkeypatch):
    """正常工具调用轮不得被标成零进展（否则日志噪音淹没真故障）"""
    got = _capture_llm_log(
        monkeypatch,
        where="invoke", duration_ms=2931.5, state=None,
        prompt_tokens=6735, completion_tokens=478, reasoning_tokens=308,
        finish_reason="tool-calls", tool_calls=2, has_content=True,
    )
    assert got["level"] == "INFO"
    assert "no_progress" not in got["data"]


def test_llm_log_final_answer_not_flagged(monkeypatch):
    """有实质 content 的 length（真·截断的最终回答）不算零进展"""
    got = _capture_llm_log(
        monkeypatch,
        where="invoke", duration_ms=100.0, state=None,
        prompt_tokens=100, completion_tokens=8192, reasoning_tokens=0,
        finish_reason="length", tool_calls=0, has_content=True,
    )
    assert got["level"] == "INFO"
    assert "no_progress" not in got["data"]


def test_llm_log_records_effective_output_cap(monkeypatch):
    """输出上限必须入链 —— 排查「为什么总是撞 8192」时唯一的硬证据。

    真正的天花板是 min(模型目录声明, LLM_MAX_TOKENS)：只看 `.env` 会被 min()骗过去，
    目录声明才是硬约束（2026-10-03 事故：目录里 DeepSeek 仍写 8192，配置调 16K 无效）。
    """
    got = _capture_llm_log(
        monkeypatch,
        where="invoke", duration_ms=100.0, state=None,
        prompt_tokens=100, completion_tokens=100, reasoning_tokens=0,
        finish_reason="stop", tool_calls=0, has_content=True, max_tokens=16384,
    )
    assert got["data"]["max_tokens"] == 16384


def test_llm_log_omits_output_cap_when_unknown(monkeypatch):
    """未解析出上限时不写脏字段（0/None 不入 data，避免日志里出现假 cap=0）"""
    got = _capture_llm_log(
        monkeypatch,
        where="invoke", duration_ms=100.0, state=None,
        prompt_tokens=100, completion_tokens=100, reasoning_tokens=0,
        finish_reason="stop", tool_calls=0, has_content=True, max_tokens=0,
    )
    assert "max_tokens" not in got["data"]


# ── 2. _generate 收尾记 generate.zero_progress 节点 ────────────────────────

@pytest.fixture()
def agent():
    """只带 _chainlog_zero_progress 的最小壳，避免拉起完整 Agent 运行时。"""
    obj = gen_mod.RAGAgentGenerate.__new__(gen_mod.RAGAgentGenerate)
    obj._usage_accum = {"input": 13987, "output": 8670, "reasoning": 8500,
                        "cache_read": 8832, "cache_write": 5155}
    obj._cost_accum = 0.0195
    return obj


def test_zero_progress_node_recorded(agent, monkeypatch):
    import app

    got = {}
    fake = types.SimpleNamespace(log=lambda level, stage, component, event, **kw: got.update(
        level=level, stage=stage, component=component, event=event, **kw))
    monkeypatch.setattr(app, "chainlog", fake, raising=False)

    agent._chainlog_zero_progress(
        state=None, model="deepseek/deepseek-v4-flash", finish_reason="length",
        rounds=1, input_tokens=13987, output_tokens=8670, reasoning_tokens=8500,
        cost=0.0195, answer_chars=27908, from_reasoning=True,
    )

    assert got["level"] == "ERROR"
    assert got["event"] == "generate.zero_progress"
    assert got["data"]["tools_invoked"] == 0
    assert got["data"]["cause"] == "reasoning_budget_exhausted"
    assert got["data"]["rounds"] == 1
    assert got["data"]["answer_from_reasoning"] is True
    assert got["data"]["reasoning_ratio"] > 0.9
    assert got["data"]["answer_chars"] == 27908
    assert "零进展" in got["message"]


def test_zero_progress_cause_when_not_reasoning_bound(agent, monkeypatch):
    import app

    got = {}
    fake = types.SimpleNamespace(log=lambda level, stage, component, event, **kw: got.update(**kw))
    monkeypatch.setattr(app, "chainlog", fake, raising=False)

    agent._chainlog_zero_progress(
        state=None, model="m", finish_reason="length",
        rounds=0, input_tokens=10, output_tokens=100, reasoning_tokens=5,
        cost=0.0, answer_chars=0,
    )
    assert got["data"]["cause"] == "length_without_tool_calls"


def test_zero_progress_cause_stop_without_reasoning_token_reporting(agent, monkeypatch):
    """实测形态 A：finish=stop，usage 不单独上报 reasoning token。

    2026-10-03 全链路实测抓到 deepseek-v4-flash 两轮各 ~6.5k token、
    `finish_reason=stop`、content 为空（正文来自 reasoning 回退），
    而 `reasoning_tokens/output_tokens` 仅 0.023。按比例判会把归因错标成
    `length_without_tool_calls`，把排查方向误导到「调小/调大输出上限」。
    """
    import app

    got = {}
    fake = types.SimpleNamespace(log=lambda level, stage, component, event, **kw: got.update(**kw))
    monkeypatch.setattr(app, "chainlog", fake, raising=False)

    agent._chainlog_zero_progress(
        state=None, model="deepseek/deepseek-v4-flash", finish_reason="stop",
        rounds=1, input_tokens=5198, output_tokens=13151, reasoning_tokens=302,
        cost=0.02, answer_chars=26000, from_reasoning=True,
    )

    # 思考规模远低于门槛（302 < 2000）→ 不是预算耗尽，而是模型自己收尾
    assert got["data"]["cause"] == "stop_without_output"
    assert got["data"]["reasoning_tokens_reported"] is False
    # 文案不得断言「全被思考占用」以外的内容，也不得谎称被截断
    assert "截断" not in got["message"]
    assert "零进展" in got["message"]


def test_zero_progress_cause_small_final_round_not_budget_exhaustion(agent, monkeypatch):
    """实测形态 B（真实用户任务 ses_208egtw0xz58cf43d）：末轮空响应但整轮干了活。

    8 轮工具调用成功写入 5 个文件后，末轮只吐 346 token 思考（rt=ct=346）
    就 finish=stop 空响应。聚合口径 rt/out=0.58 也不支持「预算耗尽」，
    但 `from_reasoning=True`。仅凭 from_reasoning 归因为「思考耗尽预算」会让
    用户去调 LLM_MAX_TOKENS —— 而实际预算只用了 346/16384，方向完全错。
    """
    import app

    got = {}
    fake = types.SimpleNamespace(log=lambda level, stage, component, event, **kw: got.update(**kw))
    monkeypatch.setattr(app, "chainlog", fake, raising=False)

    agent._chainlog_zero_progress(
        state=None, model="deepseek/deepseek-v4-flash", finish_reason="stop",
        rounds=8, input_tokens=51982, output_tokens=16645, reasoning_tokens=9605,
        cost=0.42, answer_chars=346, from_reasoning=True,
    )
    assert got["data"]["cause"] == "stop_without_output"
    assert "思考占用" not in got["message"]


def test_zero_progress_cause_stop_without_output(agent, monkeypatch):
    """finish=stop 且无 reasoning 回退痕迹：预算够但模型没用对（既无工具也无正文）。"""
    import app

    got = {}
    fake = types.SimpleNamespace(log=lambda level, stage, component, event, **kw: got.update(**kw))
    monkeypatch.setattr(app, "chainlog", fake, raising=False)

    agent._chainlog_zero_progress(
        state=None, model="m", finish_reason="stop",
        rounds=0, input_tokens=10, output_tokens=500, reasoning_tokens=5,
        cost=0.0, answer_chars=0, from_reasoning=False,
    )
    assert got["data"]["cause"] == "stop_without_output"


def test_zero_progress_rescue_event_marks_retry_in_message(agent, monkeypatch):
    """rescue 节点要标明「已注入强制输出提示」，避免与最终零进展节点混淆。"""
    import app

    got = {}
    fake = types.SimpleNamespace(log=lambda level, stage, component, event, **kw: got.update(
        level=level, event=event, **kw))
    monkeypatch.setattr(app, "chainlog", fake, raising=False)

    agent._chainlog_zero_progress(
        state=None, model="m", finish_reason="length",
        rounds=0, input_tokens=10, output_tokens=100, reasoning_tokens=5,
        cost=0.0, answer_chars=0, event="generate.zero_progress_rescue",
    )
    assert got["event"] == "generate.zero_progress_rescue"
    assert "强制输出提示" in got["message"]


def test_zero_progress_cause_helper_prefers_from_reasoning_over_ratio():
    """纯函数级锁定归因优先级：思考规模够大才是「预算耗尽」。"""
    # 真耗尽：思考吃满输出大头且规模够（8500/8670 实测经典形态）
    assert gen_mod._zero_progress_cause(
        from_reasoning=True, finish_reason="stop",
        output_tokens=8670, reasoning_tokens=8500,
    ) == "reasoning_budget_exhausted"
    # 无回退痕迹 + 思考规模够 + length
    assert gen_mod._zero_progress_cause(
        from_reasoning=False, finish_reason="length",
        output_tokens=8670, reasoning_tokens=8500,
    ) == "reasoning_budget_exhausted"
    # 有回退痕迹但思考规模很小 → 模型自己收尾（实测末轮 346 token）
    assert gen_mod._zero_progress_cause(
        from_reasoning=True, finish_reason="stop",
        output_tokens=16645, reasoning_tokens=9605,
    ) == "stop_without_output"
    # 无回退痕迹 + 思考规模小 + length
    assert gen_mod._zero_progress_cause(
        from_reasoning=False, finish_reason="length",
        output_tokens=1000, reasoning_tokens=10,
    ) == "length_without_tool_calls"
    # 无回退痕迹 + 思考规模小 + stop
    assert gen_mod._zero_progress_cause(
        from_reasoning=False, finish_reason="stop",
        output_tokens=1000, reasoning_tokens=10,
    ) == "stop_without_output"


def test_zero_progress_never_raises(agent, monkeypatch):
    """日志故障绝不能把主流程带崩（与 chainlog 整体约定一致）"""
    import app

    def _boom(*a, **k):
        raise RuntimeError("chainlog down")

    monkeypatch.setattr(app, "chainlog", types.SimpleNamespace(log=_boom), raising=False)
    agent._chainlog_zero_progress(
        state=None, model="m", finish_reason="length", rounds=0, input_tokens=0,
        output_tokens=0, reasoning_tokens=0, cost=0.0, answer_chars=0,
    )

