# -*- coding: utf-8 -*-
"""sub_tools.py 全量用例：_trim_messages 裁剪、run_tool 权限桥、tool_loop_chat
（工具循环/doom-loop/MAX_STEPS/事件上报/工作目录 set-reset）。

覆盖（含已修复缺陷）：工具执行异常经 gather 捕获为 Exception 时必须隔离，
不得因解包 (id, result) 失败拖垮整轮。

运行：pytest tests/test_sub_tools.py
"""
import asyncio
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

import app.agent.sub_tools as st
from app.permission import NeedsPermission
from app.tools import file_tools as fs


# ── _trim_messages ─────────────────────────────────────────────────────────

def test_trim_small_unchanged():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    assert st._trim_messages(msgs) is msgs


# ── strip_tool_call_markup ─────────────────────────────────────────────────

def test_strip_tool_call_markup_clean_passthrough():
    assert st.strip_tool_call_markup("正常回答") == "正常回答"
    assert st.strip_tool_call_markup("") == ""


def test_strip_tool_call_markup_fullwidth_bars():
    dirty = "<｜｜tool_calls>\n<｜｜invoke name=\"tool_read_file\">\n" \
            "<｜｜parameter name=\"path\" value=\"x\"</｜｜parameter>\n<｜｜/invoke｜>\n" \
            "<｜｜tool_results>\n<｜｜designate obj=\"x\"|>ok</｜｜/designate｜>\n补一句总结"
    cleaned = st.strip_tool_call_markup(dirty)
    assert "tool_calls" not in cleaned and "invoke" not in cleaned
    assert "补一句总结" in cleaned


def test_strip_tool_call_markup_ascii_bars_and_pure_block():
    a = "<|tool_calls|>\n<|call|>1</|call|>\n<|tool_results|>\n后文"
    assert st.strip_tool_call_markup(a) == "\n后文"
    pure = "<|tool_calls|>\n<|call|>1</|call|>"
    assert st.strip_tool_call_markup(pure) == ""


def test_trim_large_keeps_head_and_recent(monkeypatch):
    monkeypatch.setattr(st, "estimate_tokens", lambda s: (len(s) or 1) * 5)
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "user"}]
    for i in range(8):  # 8 个旧轮（内容大 → 超预算）
        msgs.append({"role": "assistant", "content": "x" * 500, "tool_calls": [{"id": f"old{i}"}]})
        msgs.append({"role": "tool", "tool_call_id": f"old{i}", "content": "r" * 500})
    for i in range(4):  # 4 个最近轮（内容小 → 保留）
        msgs.append({"role": "assistant", "content": f"a{i}", "tool_calls": [{"id": f"c{i}"}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"r{i}"})
    out = st._trim_messages(msgs)
    assert out[0] is msgs[0] and out[1] is msgs[1]  # head 保留
    assert out[-1]["role"] == "tool"
    assert out[-1]["content"] == "r3"
    assert all("old" not in str(m.get("content")) for m in out)


def test_trim_drops_tools_when_still_oversized(monkeypatch):
    monkeypatch.setattr(st, "estimate_tokens", lambda s: 5000)
    monkeypatch.setattr(st, "_SUB_CTX_KEEP_ROUNDS", 4)
    monkeypatch.setattr(st, "_SUB_CTX_MAX_TOKENS", 1000)
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for i in range(6):
        msgs.append({"role": "assistant", "content": f"a{i}", "tool_calls": []})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x"})
    out = st._trim_messages(msgs)
    assert all(m.get("role") != "tool" for m in out)


# ── _sub_agent_max_rounds / _coerce_args ───────────────────────────────────

def test_sub_agent_max_rounds(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "max_tool_rounds", 2)
    assert st._sub_agent_max_rounds() == 2
    monkeypatch.setattr(settings, "max_tool_rounds", 0)
    assert st._sub_agent_max_rounds() >= 2


def test_coerce_args_filters_unknown():
    def fn(path, limit=10):
        pass
    assert st._coerce_args(fn, {"path": "/x", "bogus": 1, "limit": 3}) == {"path": "/x", "limit": 3}


# ── run_tool ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_tool_unknown():
    assert "unknown tool" in await st.run_tool("tool_nope", {})


@pytest.mark.asyncio
async def test_run_tool_executes_and_unwraps(monkeypatch):
    def probe(**kw):
        return {"title": "t", "metadata": {}, "output": "RESULT"}
    monkeypatch.setattr(fs, "tool_probe", probe, raising=False)
    assert await st.run_tool("tool_probe", {}) == "RESULT"


@pytest.mark.asyncio
async def test_run_tool_permission_no_queue(monkeypatch):
    def probe(**kw):
        raise NeedsPermission("/ext/f.txt", "read", "tool_probe")
    monkeypatch.setattr(fs, "tool_probe", probe, raising=False)

    class Mgr:
        def create_request(self, path, operation, name, args, session_id=""):
            return SimpleNamespace(id="r1")
    monkeypatch.setattr(st, "get_perm_mgr", lambda: Mgr())
    r = await st.run_tool("tool_probe", {}, None)
    assert "Permission denied" in r


@pytest.mark.asyncio
async def test_run_tool_permission_allowed_retry(monkeypatch):
    n = {"v": 0}

    def probe(**kw):
        n["v"] += 1
        if n["v"] == 1:
            raise NeedsPermission("/ext/f.txt", "read", "tool_probe")
        return {"title": "t", "metadata": {}, "output": "OK"}
    monkeypatch.setattr(fs, "tool_probe", probe, raising=False)

    class Mgr:
        def create_request(self, path, operation, name, args, session_id=""):
            return SimpleNamespace(id="r1")

        async def await_decision(self, request_id):
            return "allowed"

        def add_temp_approval(self, path):
            self.appr = path
    mgr = Mgr()
    monkeypatch.setattr(st, "get_perm_mgr", lambda: mgr)
    q = asyncio.Queue()
    r = await st.run_tool("tool_probe", {}, q)
    assert r == "OK"
    assert mgr.appr == "/ext/f.txt"
    assert not q.empty()  # permission_request 已上报


@pytest.mark.asyncio
async def test_run_tool_permission_denied_decision(monkeypatch):
    def probe(**kw):
        raise NeedsPermission("/ext/f.txt", "read", "tool_probe")
    monkeypatch.setattr(fs, "tool_probe", probe, raising=False)

    class Mgr:
        def create_request(self, path, operation, name, args, session_id=""):
            return SimpleNamespace(id="r1")

        async def await_decision(self, request_id):
            return "denied"
    monkeypatch.setattr(st, "get_perm_mgr", lambda: Mgr())
    r = await st.run_tool("tool_probe", {}, asyncio.Queue())
    assert "was not approved" in r


@pytest.mark.asyncio
async def test_run_tool_generic_error(monkeypatch):
    def probe(**kw):
        raise ValueError("boom")
    monkeypatch.setattr(fs, "tool_probe", probe, raising=False)
    r = await st.run_tool("tool_probe", {})
    assert "Error executing tool_probe" in r and "boom" in r


# ── tool_loop_chat ─────────────────────────────────────────────────────────

def _resp(content=None, tool_calls=None, usage=None, reasoning=None):
    tcs = [
        SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=n, arguments=a))
        for i, (n, a) in enumerate(tool_calls or [])
    ]
    msg = SimpleNamespace(content=content, tool_calls=tcs or None)
    if reasoning is not None:
        msg.reasoning_content = reasoning
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=usage or SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )


@pytest.fixture
def fake_acompletion(monkeypatch):
    calls = []

    def make(script):
        async def acompletion(**kw):
            calls.append(kw)
            if not script:
                raise AssertionError("no more responses")
            return script.pop(0)
        monkeypatch.setattr(st.litellm, "acompletion", acompletion)
        return calls
    return make


@pytest.mark.asyncio
async def test_loop_chat_direct_answer(fake_acompletion):
    calls = fake_acompletion([_resp(content="  你好  ")])
    out = await st.tool_loop_chat("sys", "user")
    assert out == "你好"
    assert calls[0]["temperature"] == 0.2
    # 首轮 rnd=1 < max_rounds → 挂载工具 schema
    assert "tools" in calls[0]


@pytest.mark.asyncio
async def test_loop_chat_one_tool_round(monkeypatch, fake_acompletion):
    script = [_resp(tool_calls=[("tool_probe", '{"a":"1"}')]), _resp(content="final")]
    calls = fake_acompletion(script)
    exec_calls = []

    async def fake_run(name, args, event_queue=None):
        exec_calls.append((name, args))
        return "R"
    monkeypatch.setattr(st, "run_tool", fake_run)
    out = await st.tool_loop_chat("sys", "user")
    assert out == "final"
    assert exec_calls == [("tool_probe", {"a": "1"})]
    assert any(m.get("role") == "tool" for m in calls[-1]["messages"])


@pytest.mark.asyncio
async def test_loop_chat_history_injected(fake_acompletion):
    calls = fake_acompletion([_resp(content="done")])
    await st.tool_loop_chat("sys", "user",
                            history=[{"role": "user", "content": "prev"}, {"role": "bogus", "content": "x"}])
    roles = [m["role"] for m in calls[0]["messages"]]
    assert roles == ["system", "user", "user"]  # 非 user/assistant 被过滤
    assert calls[0]["messages"][1]["content"] == "prev"


@pytest.mark.asyncio
async def test_loop_chat_malformed_args(monkeypatch, fake_acompletion):
    script = [_resp(tool_calls=[("tool_probe", "{{{")]), _resp(content="done")]
    calls = fake_acompletion(script)
    seen = {}

    async def fake_run(name, args, event_queue=None):
        seen.update(args=args)
        return "R"
    monkeypatch.setattr(st, "run_tool", fake_run)
    out = await st.tool_loop_chat("sys", "user")
    assert out == "done"
    assert seen.get("args") == {}  # 畸形参数降级为空


@pytest.mark.asyncio
async def test_loop_chat_doom_loop(monkeypatch, fake_acompletion):
    script = [
        _resp(tool_calls=[("tool_probe", '{"a":"1"}')]),
        _resp(tool_calls=[("tool_probe", '{"a":"1"}')]),
        _resp(tool_calls=[("tool_probe", '{"a":"1"}')]),
        _resp(content="stopped"),
    ]
    calls = fake_acompletion(script)

    async def fake_run(name, args, event_queue=None):
        return "R"
    monkeypatch.setattr(st, "run_tool", fake_run)
    out = await st.tool_loop_chat("sys", "user")
    assert out == "stopped"
    texts = "".join(m.get("content", "") or "" for kw in calls for m in kw["messages"])
    assert "疑似陷入死循环" in texts


@pytest.mark.asyncio
async def test_loop_chat_max_rounds_forced_summary(monkeypatch, fake_acompletion):
    from app.config import settings
    monkeypatch.setattr(settings, "max_tool_rounds", 2)
    script = [
        _resp(tool_calls=[("tool_probe", '{"a":"1"}')]),
        _resp(tool_calls=[("tool_probe", '{"a":"1"}')]),  # 末轮仍 tool_calls → 强制收尾
        _resp(content="forced-final"),
    ]
    calls = fake_acompletion(script)

    async def fake_run(name, args, event_queue=None):
        return "R"
    monkeypatch.setattr(st, "run_tool", fake_run)
    out = await st.tool_loop_chat("sys", "user")
    assert out == "forced-final"
    # 末轮（无 tools）+ 收尾追加 MAX_STEPS
    assert "tools" not in calls[-1]
    assert "MAXIMUM STEPS REACHED" in calls[-1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_loop_chat_max_rounds_strips_tool_call_markup(monkeypatch, fake_acompletion):
    """回归：MAX_STEPS 强制收尾返回的 content 是 Hermes 工具调用块时须被剥净。"""
    from app.config import settings
    monkeypatch.setattr(settings, "max_tool_rounds", 2)
    script = [
        _resp(tool_calls=[("tool_probe", '{"a":"1"}')]),
        _resp(tool_calls=[("tool_probe", '{"a":"1"}')]),
        _resp(content="<|tool_calls|>\n<|call|>1</|call|>\n<|tool_results|>\n收尾摘要"),
    ]
    calls = fake_acompletion(script)

    async def fake_run(name, args, event_queue=None):
        return "R"
    monkeypatch.setattr(st, "run_tool", fake_run)
    out = await st.tool_loop_chat("sys", "user")
    assert out.strip() == "收尾摘要"
    assert "tool_calls" not in out
    # 实际返回的仍是强制收尾的 MAX_STEPS prompt（内容单独存在）
    assert "MAXIMUM STEPS REACHED" in calls[-1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_loop_chat_tool_error_isolated(monkeypatch, fake_acompletion):
    """回归：run_tool 抛异常被 gather 捕获为 Exception 时隔离，不拖垮整轮
    （修复前在 (tc_id, result) 解包处抛 TypeError）。"""
    script = [_resp(tool_calls=[("tool_probe", '{"a":"1"}')]), _resp(content="ok")]
    calls = fake_acompletion(script)

    async def fake_run(name, args, event_queue=None):
        raise RuntimeError("executor down")
    monkeypatch.setattr(st, "run_tool", fake_run)
    out = await st.tool_loop_chat("sys", "user")
    assert out == "ok"
    tool_msg = [m for m in calls[-1]["messages"] if m.get("role") == "tool"]
    assert "Error executing tool_probe" in tool_msg[0]["content"]


@pytest.mark.asyncio
async def test_loop_chat_directory_workspace_set_reset(fake_acompletion):
    from app.permission import current_session_workspace
    fake_acompletion([_resp(content="done")])
    assert current_session_workspace() == ""
    await st.tool_loop_chat("sys", "user", directory="/some/wd")
    assert current_session_workspace() == ""  # finally 还原


@pytest.mark.asyncio
async def test_loop_chat_empty_answer(fake_acompletion):
    fake_acompletion([_resp(content="   ")])
    assert await st.tool_loop_chat("sys", "user") == "(无回答)"


@pytest.mark.asyncio
async def test_loop_chat_reasoning_content_fallback(fake_acompletion):
    """deepseek-v4-flash 把回答写进 reasoning_content、content 为空时的回退（真实数据回归）。"""
    fake_acompletion([_resp(content="   ", reasoning="这是 reasoning 里的正史")])
    assert await st.tool_loop_chat("sys", "user") == "这是 reasoning 里的正史"


@pytest.mark.asyncio
async def test_loop_chat_reasoning_content_after_markup(fake_acompletion):
    """content 为 Hermes 工具调用块＋reasoning 有正文 → 剥块后回退到 reasoning。"""
    dirty = "<|tool_calls|>\n<|call|>1</|call|>\n<|tool_results|>\n"
    fake_acompletion([_resp(content=dirty, reasoning="剥块后的正文")])
    assert await st.tool_loop_chat("sys", "user") == "剥块后的正文"


# ── tool_loop_chat: 规则集 allowlist（对齐 opencode permission ruleset）─────────

@pytest.mark.asyncio
async def test_loop_chat_allowlist_filters_schemas(fake_acompletion):
    """allowlist 只暴露规则集内工具：写/执行工具不进 LLM 的 tools 列表。"""
    calls = fake_acompletion([_resp(content="done")])
    await st.tool_loop_chat("sys", "user", allowlist=st._READONLY_TOOL_NAMES)
    exposed = [t["function"]["name"] for t in calls[0]["tools"]]
    assert exposed == list(st._READONLY_TOOL_NAMES)
    assert "tool_write_file" not in exposed
    assert "tool_execute" not in exposed


@pytest.mark.asyncio
async def test_loop_chat_empty_allowlist_no_tools(fake_acompletion):
    """空 allowlist → 无工具可用（纯 LLM，对应 plan 规格）。"""
    calls = fake_acompletion([_resp(content="done")])
    await st.tool_loop_chat("sys", "user", allowlist=())
    assert "tools" not in calls[0]


@pytest.mark.asyncio
async def test_loop_chat_allowlist_hard_deny_at_runtime(monkeypatch, fake_acompletion):
    """即使 LLM 绕开 schema 直接调非 allowlist 工具，运行时也硬拒绝（不执行）。"""
    calls = fake_acompletion([
        _resp(tool_calls=[("tool_write_file", '{"path":"x","content":"y"}')]),
        _resp(content="final"),
    ])
    executed = []

    async def fake_run(name, args, event_queue=None):
        executed.append((name, args))
        return "EXECUTED"
    monkeypatch.setattr(st, "run_tool", fake_run)
    out = await st.tool_loop_chat("sys", "user", allowlist=st._READONLY_TOOL_NAMES)
    assert out == "final"
    assert executed == []  # 硬拒绝，未执行
    tool_msg = [m for m in calls[1]["messages"] if m.get("role") == "tool"]
    assert tool_msg and "not allowed by this agent's tool ruleset" in tool_msg[0]["content"]


@pytest.mark.asyncio
async def test_loop_chat_allowlist_default_full_set(fake_acompletion):
    """allowlist=None → 全量工具（build/web_search 等未被规则的子 Agent）。"""
    calls = fake_acompletion([_resp(content="done")])
    await st.tool_loop_chat("sys", "user")
    exposed = [t["function"]["name"] for t in calls[0]["tools"]]
    assert set(exposed) == set(st._ALL_TOOL_NAMES)


# ── tool_task 委派（对齐 opencode task：plan → explore）────────────────────────

class _FakeBus:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def send_and_wait(self, msg, timeout=None):
        self.calls.append(msg)
        return self.reply


@pytest.mark.asyncio
async def test_run_task_tool_rejects_disallowed_subagent():
    """subagent_type 不在白名单（如 plan 只允许 explore）→ 拒绝，不发送。"""
    class B:
        async def send_and_wait(self, msg, timeout=None):
            raise AssertionError("不应发送")
    r = await st._run_task_tool({"prompt": "p", "subagent_type": "build"}, B(), ("explore",))
    assert "not allowed" in r


@pytest.mark.asyncio
async def test_run_task_tool_no_bus():
    r = await st._run_task_tool({"prompt": "p", "subagent_type": "explore"}, None, ("explore",))
    assert "bus is unavailable" in r


@pytest.mark.asyncio
async def test_run_task_tool_success_envelope():
    from app.agent.base import AgentMessage
    bus = _FakeBus(AgentMessage(source="explore", target="plan", type="response", action="chat",
                                payload={"answer": "代码库事实：核心在 app/agent/supermod/core.py"},
                                thread_id="t"))
    r = await st._run_task_tool(
        {"prompt": "探索代码库", "subagent_type": "explore"}, bus, ("explore",),
        source="plan", conversation_id="cid", directory="/wd",
    )
    assert 'state="completed"' in r and "代码库事实" in r
    sent = bus.calls[0]
    assert sent.target == "explore" and sent.source == "plan"
    assert sent.payload["question"] == "探索代码库"
    assert sent.payload["conversation_id"] == "cid"
    assert sent.payload["directory"] == "/wd"


@pytest.mark.asyncio
async def test_run_task_tool_error_envelope():
    from app.agent.base import AgentMessage
    bus = _FakeBus(AgentMessage(source="explore", target="plan", type="error", action="chat",
                                payload={"error": "explore 挂了"}, thread_id="t"))
    r = await st._run_task_tool({"prompt": "p", "subagent_type": "explore"}, bus, ("explore",))
    assert 'state="error"' in r and "explore 挂了" in r


@pytest.mark.asyncio
async def test_loop_chat_exposes_tool_task_with_delegation(fake_acompletion):
    """plan 规格：allowlist=(tool_task,) + bus + task_subagents → 暴露 tool_task schema。"""
    calls = fake_acompletion([_resp(content="done")])
    await st.tool_loop_chat("sys", "user", allowlist=("tool_task",), bus=object(),
                            task_subagents=("explore",))
    exposed = [t["function"]["name"] for t in calls[0]["tools"]]
    assert exposed == ["tool_task"]


@pytest.mark.asyncio
async def test_loop_chat_dispatches_tool_task(monkeypatch, fake_acompletion):
    """LLM 调 tool_task → 走 _run_task_tool 委派 handler（不走 run_tool 文件工具）。"""
    fake_acompletion([
        _resp(tool_calls=[("tool_task", '{"prompt":"p","subagent_type":"explore"}')]),
        _resp(content="final"),
    ])
    seen = {}

    async def fake_task(args, bus, allowed, **kw):
        seen["args"] = args
        seen["allowed"] = allowed
        return "<task id=\"x\" state=\"completed\"><task_result>ok</task_result></task>"
    monkeypatch.setattr(st, "_run_task_tool", fake_task)

    async def boom_run(*a, **k):
        raise AssertionError("不应走 run_tool")
    monkeypatch.setattr(st, "run_tool", boom_run)

    out = await st.tool_loop_chat("sys", "user", allowlist=("tool_task",), bus=object(),
                                  task_subagents=("explore",))
    assert out == "final"
    assert seen["args"]["subagent_type"] == "explore"
    assert seen["allowed"] == ("explore",)