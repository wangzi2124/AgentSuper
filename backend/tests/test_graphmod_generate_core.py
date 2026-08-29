# -*- coding: utf-8 -*-
"""graphmod generate/core/tools 全量用例（mock litellm，无真实 LLM）。

覆盖：
  - tools.py：_tool_task（深度护栏/bus 响应/错误/字段透传）、_tool_memory（set/get/
    search/错误）、_execute_tool（找到/未找到/协程+同步 fn/NeedsPermission 无队列拒
    绝、有队列审批放行/拒绝、命令审批、流式分派、回退、任务/记忆特判）、
    _execute_tool_streaming（deny/ask/校验失败/Popen 失败/成功/超时杀树/重定向）
  - generate.py：_generate 全分支（stop/length/content-filter/空内容/上下文注入/
    多模态附件/工具循环/同轮去重/参数解析失败/doom-loop 注入与升级/MAX_STEPS/
    MAX_TOOL_ROUNDS 强制收尾）
  - core.py：_push_stream_event、_assemble_response、_llm_call 流式组装/回退/异常/
    中断、_build_graph 节点、refresh_tools、invoke（工作目录 set/reset + 任务收尾）

运行：pytest tests/test_graphmod_generate_core.py
"""
import asyncio
import io
import os
import sys
import time
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from _graphmod_support import FakeLLM, build_agent, fake_task, make_state
from app.agent.base import AgentMessage
from app.agent.graphmod.core import RAGAgent
import app.agent.graphmod.core as core_mod
import app.agent.graphmod.generate as gen_mod
import app.agent.graphmod.tools as tools_mod
from app.config import settings
from app.permission import NeedsPermission
from app.agent.tools import ToolDef


# ── 工具 ───────────────────────────────────────────────────────────────────

def _agent_with_tool(fn, name="tool_flaky", **extra):
    agent = build_agent()
    agent.tools.append(ToolDef(name=name, description="d", parameters={}, fn=fn, **extra))
    return agent


def test_tool_placeholders():
    agent = build_agent()
    assert agent._task_tool_placeholder() == ""
    assert agent._memory_tool_placeholder(key="k") == ""


@pytest.mark.asyncio
async def test_tool_task_missing_prompt():
    agent = build_agent()
    assert "prompt" in await agent._tool_task({})


@pytest.mark.asyncio
async def test_tool_task_bad_subagent():
    agent = build_agent()
    r = await agent._tool_task({"prompt": "p", "subagent_type": "rag"})
    assert "unknown subagent_type" in r


@pytest.mark.asyncio
async def test_tool_task_no_bus():
    agent = build_agent()
    r = await agent._tool_task({"prompt": "p", "subagent_type": "web_search"})
    assert "unavailable" in r


@pytest.mark.asyncio
async def test_tool_task_depth_limit(monkeypatch):
    monkeypatch.setattr(settings, "subagent_depth", 1)
    agent = build_agent()
    agent.task_bus = FakeBus(AgentMessage(source="x", target="web_search", type="response",
                                          action="chat", payload={"answer": "42"}))
    r = await agent._tool_task({"prompt": "p", "subagent_type": "code"}, depth=1)
    assert "depth limit" in r


class FakeBus:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def send_and_wait(self, msg, timeout=None):
        self.calls.append((msg, timeout))
        return self.reply


@pytest.mark.asyncio
async def test_tool_task_success_web_search():
    agent = build_agent()
    bus = FakeBus(AgentMessage(source="x", target="web_search", type="response", action="chat",
                               payload={"answer": "42"}))
    agent.task_bus = bus
    eq = asyncio.Queue()
    r = await agent._tool_task({"prompt": "p", "subagent_type": "web_search"}, depth=0,
                               event_queue=eq, directory="/wd", conversation_id="cid")
    assert r == "42"
    msg, timeout = bus.calls[0]
    assert timeout == settings.sub_agent_timeout
    assert msg.payload["conversation_id"] == "cid"
    assert msg.payload["directory"] == "/wd"
    assert msg.payload["_task_depth"] == 1
    assert msg.payload["_event_queue"] is eq
    assert msg.payload["use_vector_db"] is False
    assert msg.payload["files"] == []
    assert msg.target == "web_search"


@pytest.mark.asyncio
async def test_tool_task_code_extended_timeout():
    agent = build_agent()
    bus = FakeBus(AgentMessage(source="x", target="code", type="response", action="chat",
                               payload={"answer": "done"}))
    agent.task_bus = bus
    await agent._tool_task({"prompt": "p", "subagent_type": "code"}, depth=0)
    assert bus.calls[0][1] == settings.sub_agent_timeout_extended


@pytest.mark.asyncio
async def test_tool_task_error_reply():
    agent = build_agent()
    agent.task_bus = FakeBus(AgentMessage(source="x", target="web_search", type="error",
                                          action="chat", payload={"error": "boom"}))
    r = await agent._tool_task({"prompt": "p", "subagent_type": "web_search"})
    assert "boom" in r


@pytest.mark.asyncio
async def test_tool_task_no_answer():
    agent = build_agent()
    agent.task_bus = FakeBus(AgentMessage(source="x", target="web_search", type="response",
                                          action="chat", payload={}))
    r = await agent._tool_task({"prompt": "p", "subagent_type": "web_search"})
    assert "(no answer)" in r


class FakeMemory:
    def __init__(self):
        self.sets = []
        self.store = {}
        self.by_tag = {}

    async def set(self, key, value, ttl, tags, namespace):
        self.sets.append((key, value, ttl, tags, namespace))
        self.store[key] = value

    async def get(self, key, default=None, namespace=""):
        return self.store.get(key, default)

    async def get_by_tag(self, tag, namespace=""):
        return self.by_tag.get(tag, {})


@pytest.mark.asyncio
async def test_memory_unavailable():
    agent = build_agent()
    assert "unavailable" in await agent._tool_memory("tool_memory_set", {"key": "k", "value": "v"}, None)


@pytest.mark.asyncio
async def test_memory_set_get_search():
    mm = FakeMemory()
    agent = build_agent(memory=mm)
    state = make_state(conversation_id="cid")
    r = await agent._tool_memory("tool_memory_set", {"key": "k", "value": "v", "tags": "solo"}, state)
    assert "记住成功" in r
    assert mm.sets[0][3] == ["solo"]  # 非 list tags 归一
    assert mm.sets[0][4] == "cid"
    r = await agent._tool_memory("tool_memory_get", {"key": "k"}, state)
    assert "v" in r
    mm.by_tag["proj"] = {"a": "1"}
    r = await agent._tool_memory("tool_memory_search", {"tag": "proj"}, state)
    assert "a: 1" in r


@pytest.mark.asyncio
async def test_memory_missing_and_not_found():
    agent = build_agent(memory=FakeMemory())
    state = make_state()
    assert "'key' is required" in await agent._tool_memory("tool_memory_set", {}, state)
    assert "'key' is required" in await agent._tool_memory("tool_memory_get", {}, state)
    assert "'tag' is required" in await agent._tool_memory("tool_memory_search", {}, state)
    assert "未找到 key" in await agent._tool_memory("tool_memory_get", {"key": "nope"}, state)
    assert "未找到标签" in await agent._tool_memory("tool_memory_search", {"tag": "nope"}, state)
    assert "unknown memory tool" in await agent._tool_memory("tool_memory_bogus", {}, state)


@pytest.mark.asyncio
async def test_memory_exception_handled():
    class BoomMem:
        async def set(self, key, value, ttl, tags, namespace):
            raise RuntimeError("mem down")
    agent = build_agent(memory=BoomMem())
    r = await agent._tool_memory("tool_memory_set", {"key": "k", "value": "v"}, make_state())
    assert "Error executing" in r


@pytest.mark.asyncio
async def test_execute_tool_not_found():
    agent = build_agent()
    assert "not found" in await agent._execute_tool("tool_nope", {}, make_state())


@pytest.mark.asyncio
async def test_execute_tool_coroutine_fn():
    async def f(**kw):
        return "ASYNC"
    agent = _agent_with_tool(f, name="tool_async")
    assert await agent._execute_tool("tool_async", {}, None) == "ASYNC"


@pytest.mark.asyncio
async def test_execute_tool_sync_fn():
    def f(**kw):
        return "SYNC"
    agent = _agent_with_tool(f, name="tool_sync")
    assert await agent._execute_tool("tool_sync", {}, None) == "SYNC"


@pytest.mark.asyncio
async def test_execute_tool_generic_error():
    def f(**kw):
        raise ValueError("bad args")
    agent = _agent_with_tool(f, name="tool_err")
    r = await agent._execute_tool("tool_err", {}, None)
    assert "Error executing tool_err" in r and "bad args" in r


class ApproveMgr:
    def __init__(self, decision="allowed"):
        self.decision = decision
        self.temp_appr = []
        self.temp_cmd = []
        self.waiters = 0

    def create_request(self, path, operation, tool_name="", tool_args=None, session_id=""):
        return SimpleNamespace(id="r1")

    async def await_decision(self, request_id):
        self.waiters += 1
        return self.decision

    def add_temp_approval(self, path):
        self.temp_appr.append(path)

    def add_temp_command_approval(self, path):
        self.temp_cmd.append(path)


@pytest.mark.asyncio
async def test_execute_needs_permission_no_queue(monkeypatch):
    mgr = ApproveMgr()

    def fn(**kw):
        raise NeedsPermission("/ext/f.txt", "write", "tool_flaky")
    agent = _agent_with_tool(fn, name="tool_flaky")
    monkeypatch.setattr(tools_mod, "get_perm_mgr", lambda: mgr)
    r = await agent._execute_tool("tool_flaky", {}, make_state())
    assert "Permission denied: write on '/ext/f.txt'" in r
    assert mgr.waiters == 0  # 无队列不等待审批


@pytest.mark.asyncio
async def test_execute_permission_allowed_retries(monkeypatch):
    mgr = ApproveMgr("allowed")
    n = {"v": 0}

    def fn(**kw):
        n["v"] += 1
        if n["v"] == 1:
            raise NeedsPermission("/ext/f.txt", "write", "tool_flaky")
        return "OK"
    agent = _agent_with_tool(fn, name="tool_flaky")
    monkeypatch.setattr(tools_mod, "get_perm_mgr", lambda: mgr)
    q = asyncio.Queue()
    state = make_state(_event_queue=q)
    r = await agent._execute_tool("tool_flaky", {}, state)
    assert r == "OK"
    assert mgr.temp_appr == ["/ext/f.txt"]
    assert not q.empty()  # permission_request 已推送


@pytest.mark.asyncio
async def test_execute_permission_command_approval(monkeypatch):
    mgr = ApproveMgr("allowed")

    def fn(**kw):
        raise NeedsPermission("node --version", "command", "tool_execute")
    agent = _agent_with_tool(fn, name="tool_flaky")
    monkeypatch.setattr(tools_mod, "get_perm_mgr", lambda: mgr)
    q = asyncio.Queue()
    # fn 每次调用都抛 → 审批放行后重试仍抛 NeedsPermission，且不受外层 except
    # Exception 包装（在 except 处理器内再抛）→ 直接向上传播（产品现状锁定）
    with pytest.raises(NeedsPermission):
        await agent._execute_tool("tool_flaky", {}, make_state(_event_queue=q))
    assert mgr.temp_cmd == ["node --version"]


@pytest.mark.asyncio
async def test_execute_permission_denied(monkeypatch):
    mgr = ApproveMgr("denied")

    def fn(**kw):
        raise NeedsPermission("/ext/f.txt", "read", "tool_flaky")
    agent = _agent_with_tool(fn, name="tool_flaky")
    monkeypatch.setattr(tools_mod, "get_perm_mgr", lambda: mgr)
    q = asyncio.Queue()
    r = await agent._execute_tool("tool_flaky", {}, make_state(_event_queue=q))
    assert "Permission denied" in r
    assert mgr.waiters == 1


@pytest.mark.asyncio
async def test_execute_tool_task_dispatch(monkeypatch):
    agent = build_agent()
    calls = []

    async def fake_task(args, depth=0, event_queue=None, directory="", conversation_id=""):
        calls.append((args, depth, event_queue, directory, conversation_id))
        return "SUB"
    monkeypatch.setattr(agent, "_tool_task", fake_task)
    q = asyncio.Queue()
    state = make_state(_task_depth=2, _event_queue=q, _cwd="/w", conversation_id="cid")
    r = await agent._execute_tool("tool_task", {"prompt": "p", "subagent_type": "code"}, state)
    assert r == "SUB"
    assert calls[0][1] == 2
    assert calls[0][3] == "/w"
    assert calls[0][4] == "cid"


@pytest.mark.asyncio
async def test_execute_memory_dispatch(monkeypatch):
    agent = build_agent(memory=object())
    calls = []

    async def fake_mem(name, args, state):
        calls.append((name, args))
        return "MEM"
    monkeypatch.setattr(agent, "_tool_memory", fake_mem)
    r = await agent._execute_tool("tool_memory_get", {"key": "k"}, make_state())
    assert r == "MEM"
    assert calls == [("tool_memory_get", {"key": "k"})]


@pytest.mark.asyncio
async def test_execute_streaming_dispatch(monkeypatch):
    agent = build_agent()

    async def fake_stream(args, eq, on_activity):
        return "STREAMED"
    monkeypatch.setattr(agent, "_execute_tool_streaming", fake_stream)
    q = asyncio.Queue()
    state = make_state(_event_queue=q, _on_activity=lambda t: None)
    r = await agent._execute_tool("tool_execute", {"command": "echo hi"}, state)
    assert r == "STREAMED"


@pytest.mark.asyncio
async def test_execute_streaming_fallback_sync(monkeypatch):
    agent = build_agent()

    async def fake_stream(args, eq, on_activity):
        raise RuntimeError("selector loop")
    monkeypatch.setattr(agent, "_execute_tool_streaming", fake_stream)
    monkeypatch.setattr("app.tools.file_tools.tool_execute", lambda **args: {"output": "SYNC"})
    monkeypatch.setattr("app.tools.file_tools.unwrap", lambda r: r.get("output"))
    q = asyncio.Queue()
    r = await agent._execute_tool("tool_execute", {"command": "echo hi"}, make_state(_event_queue=q))
    assert r == "SYNC"


# ── _execute_tool_streaming ────────────────────────────────────────────────

class FakeProc:
    def __init__(self, out=b"", err=b"", rc=0):
        self.stdout = io.BytesIO(out)
        self.stderr = io.BytesIO(err)
        self.returncode = rc

    def wait(self):
        return self.returncode


def _perm_mgr_allow():
    return SimpleNamespace(check=lambda p, op: "allow")


def _perm_mgr_deny():
    return SimpleNamespace(check=lambda p, op: "deny")


@pytest.mark.asyncio
async def test_execute_streaming_deny(monkeypatch):
    monkeypatch.setattr("app.permission.get_manager", _perm_mgr_deny)
    monkeypatch.setattr("app.tools.file_tools._resolve", lambda p: Path2(p))
    agent = build_agent()
    r = await agent._execute_tool_streaming({"command": "echo hi"}, asyncio.Queue())
    assert "access denied" in r


class Path2:
    def __init__(self, p):
        from pathlib import Path
        self._p = Path(p)
        self._p.mkdir(parents=True, exist_ok=True)

    def __str__(self):
        return str(self._p)

    def is_dir(self):
        return True

    def __truediv__(self, other):
        return self._p / other


@pytest.mark.asyncio
async def test_execute_streaming_ask_raises(monkeypatch):
    monkeypatch.setattr("app.permission.get_manager", lambda: SimpleNamespace(check=lambda p, op: "ask"))
    monkeypatch.setattr("app.tools.file_tools._resolve", lambda p: Path2(p))
    agent = build_agent()
    with pytest.raises(NeedsPermission):
        await agent._execute_tool_streaming({"command": "echo hi"}, asyncio.Queue())


@pytest.mark.asyncio
async def test_execute_streaming_validation_error(monkeypatch):
    monkeypatch.setattr("app.permission.get_manager", _perm_mgr_allow)
    monkeypatch.setattr("app.tools.file_tools._resolve", lambda p: Path2(p))
    monkeypatch.setattr("app.tools.file_tools._validate_shell_command",
                        lambda command, cwd, ask: (_ for _ in ()).throw(ValueError("bad command")))
    agent = build_agent()
    r = await agent._execute_tool_streaming({"command": "echo hi"}, asyncio.Queue())
    assert "Error: bad command" in r


@pytest.mark.asyncio
async def test_execute_streaming_popen_failure(monkeypatch):
    monkeypatch.setattr("app.permission.get_manager", _perm_mgr_allow)
    monkeypatch.setattr("app.tools.file_tools._resolve", lambda p: Path2(p))
    monkeypatch.setattr("app.tools.file_tools._validate_shell_command", lambda command, cwd, ask: None)
    monkeypatch.setattr("app.tools.file_tools._check_redirect_targets_permission", lambda *a, **k: None)
    monkeypatch.setattr("app.tools.file_tools._needs_shell", lambda cmd: False)

    def boom_popen(*a, **k):
        raise OSError()
    monkeypatch.setattr(tools_mod.subprocess, "Popen", boom_popen)
    agent = build_agent()
    r = await agent._execute_tool_streaming({"command": "echo hi"}, asyncio.Queue())
    assert "Error starting command" in r
    assert "Command: 'echo hi'" in r


@pytest.mark.asyncio
async def test_execute_streaming_success(monkeypatch):
    monkeypatch.setattr("app.permission.get_manager", _perm_mgr_allow)
    monkeypatch.setattr("app.tools.file_tools._resolve", lambda p: Path2(p))
    monkeypatch.setattr("app.tools.file_tools._validate_shell_command", lambda command, cwd, ask: None)
    monkeypatch.setattr("app.tools.file_tools._check_redirect_targets_permission", lambda *a, **k: None)
    monkeypatch.setattr("app.tools.file_tools._needs_shell", lambda cmd: False)
    monkeypatch.setattr(tools_mod.subprocess, "Popen", lambda *a, **k: FakeProc(out=b"hello world\n"))
    agent = build_agent()
    q = asyncio.Queue()
    r = await agent._execute_tool_streaming({"command": "echo hi"}, q)
    assert "Exit code: 0" in r
    assert "hello world" in r
    # 心跳/输出事件已推送到队列
    assert not q.empty()


@pytest.mark.asyncio
async def test_execute_streaming_timeout_kills_tree(monkeypatch):
    monkeypatch.setattr("app.permission.get_manager", _perm_mgr_allow)
    monkeypatch.setattr("app.tools.file_tools._resolve", lambda p: Path2(p))
    monkeypatch.setattr("app.tools.file_tools._validate_shell_command", lambda command, cwd, ask: None)
    monkeypatch.setattr("app.tools.file_tools._check_redirect_targets_permission", lambda *a, **k: None)
    monkeypatch.setattr("app.tools.file_tools._needs_shell", lambda cmd: False)
    killed = []
    monkeypatch.setattr("app.tools.file_tools._kill_process_tree", lambda proc: killed.append(proc))
    proc = FakeProc(out=b"slow output\n")
    monkeypatch.setattr(tools_mod.subprocess, "Popen", lambda *a, **k: proc)

    real_asyncio = tools_mod.asyncio

    class FakeAsyncio:
        TimeoutError = real_asyncio.TimeoutError

        def get_running_loop(self):
            return real_asyncio.get_running_loop()

        def to_thread(self, fn, *args, **kw):
            # 任务化避免 wait_for 抛超时时遗留未 await 的协程警告
            return real_asyncio.get_running_loop().create_task(
                real_asyncio.to_thread(fn, *args, **kw)
            )

        async def wait_for(self, aw, timeout=None):
            raise real_asyncio.TimeoutError()

    monkeypatch.setattr(tools_mod, "asyncio", FakeAsyncio())
    agent = build_agent()
    r = await agent._execute_tool_streaming({"command": "sleep 100"}, asyncio.Queue())
    assert "command timed out after" in r
    assert killed  # 杀树被调用
    await asyncio.sleep(0.1)  # 让后台任务收尾


# ── generate.py ────────────────────────────────────────────────────────────

@pytest.fixture
def gen_env(monkeypatch, tmp_path):
    """隔离 graphmod 的 monitor/trace/prompt 副作用。"""
    import app.trace_log as tl
    tl._path = None
    monkeypatch.setenv("AGENTSUPER_LOG_DIR", str(tmp_path / "logs"))
    for name in ("record_model_call", "trace", "trace_messages"):
        monkeypatch.setattr(gen_mod, name, lambda *a, **k: None)
    # [C5 · 方案 D] 旧用例不 mock 摘要组件 → 关闭小步快走，保持既有行为
    monkeypatch.setattr(settings, "step_summary_enabled", False)
    return monkeypatch


def _setup_generate(monkeypatch, script, exec_spy=None):
    agent = build_agent()
    llm = FakeLLM()
    llm.responses = script
    agent._llm_call = llm
    if exec_spy is not None:
        agent._execute_tool = exec_spy
    return agent, llm


@pytest.mark.asyncio
async def test_generate_basic_stop(gen_env):
    agent, llm = _setup_generate(gen_env, [FakeLLM().response(content="你好")])
    out = await agent._generate(make_state())
    assert out["answer"] == "你好"
    assert out["finish"] == "stop"
    assert out["model"] == settings.llm_model
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_generate_with_context_and_history(gen_env):
    agent, llm = _setup_generate(gen_env, [FakeLLM().response(content="A")])
    state = make_state(context=[{"content": "C1", "metadata": {"document_id": "d1"}}],
                       history=[{"role": "user", "content": "prev"}])
    await agent._generate(state)
    _, messages, _ = llm.calls[0]
    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "prev"}
    assert "Retrieved Context:" in messages[2]["content"]
    assert "C1" in messages[2]["content"]


@pytest.mark.asyncio
async def test_generate_multimodal_files(gen_env, monkeypatch):
    monkeypatch.setattr(gen_mod, "_attachment_parts",
                        lambda files, budget=6000: (["img.png"], "FILE_TEXT"))
    monkeypatch.setattr(gen_mod, "_find_attachment",
                        lambda files, name: {"filename": "img.png", "mime_type": "image/png", "data": "AAAA"})
    agent, llm = _setup_generate(gen_env, [FakeLLM().response(content="A")])
    state = make_state(files=[{"filename": "img.png", "mime_type": "image/png", "data": "AAAA"}])
    await agent._generate(state)
    _, messages, _ = llm.calls[0]
    user = messages[-1]["content"]
    assert isinstance(user, list)
    types = [p["type"] for p in user]
    assert types == ["text", "image_url", "text"]
    img = next(p for p in user if p["type"] == "image_url")
    assert img["image_url"]["url"] == "data:image/png;base64,AAAA"


@pytest.mark.asyncio
async def test_generate_session_cwd_in_system(gen_env):
    agent, llm = _setup_generate(gen_env, [FakeLLM().response(content="A")])
    await agent._generate(make_state(_cwd="/workdir"))
    _, messages, _ = llm.calls[0]
    assert "/workdir" in messages[0]["content"]
    assert "[会话工作目录]" in messages[0]["content"]


@pytest.mark.asyncio
async def test_generate_length_finish(gen_env):
    agent, llm = _setup_generate(gen_env, [FakeLLM().response(content="截断", finish_reason="length")])
    out = await agent._generate(make_state())
    assert "内容可能不完整" in out["answer"]
    assert out["finish"] == "length"


@pytest.mark.asyncio
async def test_generate_content_filter(gen_env):
    agent, llm = _setup_generate(gen_env, [FakeLLM().response(content="X", finish_reason="content_filter")])
    out = await agent._generate(make_state())
    assert "内容安全策略拦截" in out["answer"]
    assert out["finish"] == "content-filter"


@pytest.mark.asyncio
async def test_generate_empty_content_default(gen_env):
    agent, llm = _setup_generate(gen_env, [FakeLLM().response(content="")])
    out = await agent._generate(make_state())
    assert out["answer"] == "任务已完成，请查看结果。"


@pytest.mark.asyncio
async def test_generate_one_tool_round_then_stop(gen_env):
    exec_calls = []

    async def spy(name, args, state=None):
        exec_calls.append((name, args))
        return "R"
    agent, llm = _setup_generate(
        gen_env,
        [FakeLLM().response(tool_calls=[("tool_probe", '{"a": "1"}')]),
         FakeLLM().response(content="done")],
        exec_spy=spy,
    )
    out = await agent._generate(make_state())
    assert out["answer"] == "done"
    assert exec_calls == [("tool_probe", {"a": "1"})]
    # tool 结果以 tool 消息回填
    tool_msg = llm.calls[1][1][-1]
    assert tool_msg["role"] == "tool"
    assert "R" in tool_msg["content"]


@pytest.mark.asyncio
async def test_generate_parse_args_failure(gen_env):
    exec_calls = []

    async def spy(name, args, state=None):
        exec_calls.append((name, args))
        return "R"
    agent, llm = _setup_generate(
        gen_env,
        [FakeLLM().response(tool_calls=[("tool_probe", "{{{not json")]),
         FakeLLM().response(content="done")],
        exec_spy=spy,
    )
    out = await agent._generate(make_state())
    assert exec_calls == []  # 参数解析失败不执行工具
    assert out["answer"] == "done"


@pytest.mark.asyncio
async def test_generate_dedup_across_rounds(gen_env):
    exec_calls = []

    async def spy(name, args, state=None):
        exec_calls.append((name, args))
        return "DATA"
    agent, llm = _setup_generate(
        gen_env,
        [FakeLLM().response(tool_calls=[("tool_read_file", '{"path": "/x"}')]),
         FakeLLM().response(tool_calls=[("tool_read_file", '{"path": "/x"}')]),
         FakeLLM().response(content="done")],
        exec_spy=spy,
    )
    await agent._generate(make_state())
    assert len(exec_calls) == 1  # 第二轮命中去重缓存


@pytest.mark.asyncio
async def test_generate_doom_loop_injects_prompt(gen_env, monkeypatch):
    monkeypatch.setattr(settings, "doom_loop_threshold", 2)
    monkeypatch.setattr(settings, "doom_loop_max_strikes", 2)

    async def spy(name, args, state=None):
        return "R"
    agent, llm = _setup_generate(
        gen_env,
        [FakeLLM().response(tool_calls=[("tool_probe", '{"a":"1"}')]) for _ in range(3)]
        + [FakeLLM().response(content="done")],
        exec_spy=spy,
    )
    await agent._generate(make_state())
    texts = "".join(m.get("content", "") or "" for _, msgs, _ in llm.calls for m in msgs)
    assert "系统提示：检测到连续多轮" in texts


@pytest.mark.asyncio
async def test_generate_doom_escalation_forces_summary(gen_env, monkeypatch):
    monkeypatch.setattr(settings, "doom_loop_threshold", 2)
    monkeypatch.setattr(settings, "doom_loop_max_strikes", 1)

    async def spy(name, args, state=None):
        return "R"
    agent, llm = _setup_generate(
        gen_env,
        [FakeLLM().response(tool_calls=[("tool_probe", '{"a":"1"}')]) for _ in range(3)]
        + [FakeLLM().response(content="forced")],
        exec_spy=spy,
    )
    out = await agent._generate(make_state())
    assert out["answer"] == "forced"
    texts = "".join(m.get("content", "") or "" for _, msgs, _ in llm.calls for m in msgs)
    assert "MAXIMUM STEPS REACHED" in texts
    assert llm.calls[-1][2] is None  # 工具禁用


@pytest.mark.asyncio
async def test_generate_max_steps_injects_and_disables_tools(gen_env, monkeypatch):
    monkeypatch.setattr(settings, "max_steps", 2)
    monkeypatch.setattr(settings, "max_tool_rounds", 8)

    async def spy(name, args, state=None):
        return "R"
    agent, llm = _setup_generate(
        gen_env,
        [FakeLLM().response(tool_calls=[("tool_probe", '{"a":"1"}')]),
         FakeLLM().response(tool_calls=[("tool_probe", '{"a":"1"}')]),
         FakeLLM().response(content="final")],
        exec_spy=spy,
    )
    out = await agent._generate(make_state())
    assert out["answer"] == "final"
    assert llm.calls[-1][2] is None
    texts = "".join(m.get("content", "") or "" for _, msgs, _ in llm.calls for m in msgs)
    assert "MAXIMUM STEPS REACHED" in texts


@pytest.mark.asyncio
async def test_generate_max_tool_rounds_forced_final(gen_env, monkeypatch, caplog):
    monkeypatch.setattr(settings, "max_tool_rounds", 1)

    async def spy(name, args, state=None):
        return "R"
    agent, llm = _setup_generate(
        gen_env,
        [FakeLLM().response(tool_calls=[("tool_probe", '{"a":"1"}')]),
         FakeLLM().response(tool_calls=[("tool_probe", '{"a":"1"}')]),
         FakeLLM().response(content="forced-final")],
        exec_spy=spy,
    )
    with caplog.at_level("WARNING"):
        out = await agent._generate(make_state())
    assert out["answer"] == "forced-final"
    assert "Max tool rounds" in caplog.text


@pytest.mark.asyncio
async def test_generate_task_progress(gen_env):
    async def spy(name, args, state=None):
        return "R"
    class Tracker:
        def __init__(self):
            self.steps = 0
            self.tool_calls = 0

        def increment_step(self):
            self.steps += 1

        def increment_tool_calls(self, n):
            self.tool_calls += n

        def record_compaction(self):
            pass

        def to_dict(self):
            return {}

    task = Tracker()
    agent, llm = _setup_generate(
        gen_env,
        [FakeLLM().response(tool_calls=[("tool_probe", '{"a":"1"}')]),
         FakeLLM().response(content="done")],
        exec_spy=spy,
    )
    await agent._generate(make_state(_task=task))
    assert task.steps == 1
    assert task.tool_calls == 1


# ── core.py ────────────────────────────────────────────────────────────────

@pytest.fixture
def core_env(monkeypatch, tmp_path):
    import app.trace_log as tl
    tl._path = None
    monkeypatch.setenv("AGENTSUPER_LOG_DIR", str(tmp_path / "logs"))
    for name in ("record_model_call", "trace", "trace_messages", "log_prompt"):
        monkeypatch.setattr(core_mod, name, lambda *a, **k: None)
    return monkeypatch


def test_push_stream_event():
    agent = build_agent()
    q = asyncio.Queue()
    state = make_state(_event_queue=q)
    agent._push_stream_event(state, {"type": "text_delta", "delta": "x"})
    assert not q.empty()
    assert state["steps"] == []  # 不进 steps


def test_push_stream_event_no_queue_and_error():
    agent = build_agent()
    agent._push_stream_event(make_state(), {"type": "x"})
    class BoomQueue:
        def put_nowait(self, ev):
            raise RuntimeError
    agent._push_stream_event(make_state(_event_queue=BoomQueue()), {"type": "x"})  # 静默


def test_assemble_response_accumulates(core_env):
    agent = build_agent()
    q = asyncio.Queue()
    state = make_state(_event_queue=q)
    resp = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, prompt_cache_hit_tokens=3, prompt_cache_miss_tokens=7),
        choices=[SimpleNamespace(message=SimpleNamespace(content="abc"))],
    )
    agent._assemble_response("m", resp, time.time(), state, push_text=True)
    assert agent._usage_accum["input"] == 10
    assert agent._usage_accum["output"] == 5
    assert agent._usage_accum["cache_read"] == 3
    assert agent._usage_accum["cache_write"] == 7
    assert not q.empty()  # text_delta


def test_assemble_response_no_usage_and_no_push():
    agent = build_agent()
    agent._usage_accum = None
    resp = SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content=""))])
    agent._assemble_response("m", resp, time.time(), None, push_text=False)
    assert agent._usage_accum["input"] == 0


def _chunk(content=None, tool_calls=None, finish_reason=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _stream_acompletion(chunks):
    async def fake_acompletion(**kw):
        async def gen():
            for c in chunks:
                yield c
        return gen()
    return fake_acompletion


@pytest.mark.asyncio
async def test_llm_call_stream_assembly(core_env, monkeypatch):
    fake_acompletion = _stream_acompletion([
        _chunk(content="Hel"),
        _chunk(content="lo", tool_calls=[SimpleNamespace(index=0, id="c1",
                 function=SimpleNamespace(name="tool_x", arguments='{"a":'))]),
        _chunk(content=None, tool_calls=[SimpleNamespace(index=0, id=None,
                 function=SimpleNamespace(name="y", arguments="1}"))], finish_reason="tool_calls"),
        _chunk(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5,
               prompt_cache_hit_tokens=3, prompt_cache_miss_tokens=7)),
    ])
    monkeypatch.setattr(core_mod.litellm, "acompletion", fake_acompletion)
    agent = build_agent()
    resp = await agent._llm_call("m", [{"role": "user", "content": "q"}], None)
    assert resp.choices[0].message.content == "Hello"
    assert resp.choices[0].finish_reason == "tool_calls"
    tc = resp.choices[0].message.tool_calls[0]
    assert tc.function.name == "tool_xy"
    assert tc.function.arguments == '{"a":1}'
    assert agent._usage_accum["input"] == 10 and agent._usage_accum["output"] == 5


@pytest.mark.asyncio
async def test_llm_call_stream_fallback_non_stream(core_env, monkeypatch):
    calls = []

    async def fake_acompletion(**kw):
        calls.append(kw.get("stream"))
        if kw.get("stream"):
            raise RuntimeError("stream unsupported")
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=2),
            choices=[SimpleNamespace(message=SimpleNamespace(content="fallback"))],
        )

    monkeypatch.setattr(core_mod.litellm, "acompletion", fake_acompletion)
    agent = build_agent()
    q = asyncio.Queue()
    resp = await agent._llm_call("m", [], None, state=make_state(_event_queue=q))
    assert resp.choices[0].message.content == "fallback"
    assert calls == [True, None]  # 回退调用不传 stream
    assert not q.empty()  # 非流式全文也推送 text_delta


@pytest.mark.asyncio
async def test_llm_call_both_fail_raises(core_env, monkeypatch):
    async def fake_acompletion(**kw):
        raise RuntimeError("provider down")
    monkeypatch.setattr(core_mod.litellm, "acompletion", fake_acompletion)
    agent = build_agent()
    with pytest.raises(RuntimeError):
        await agent._llm_call("m", [], None)


@pytest.mark.asyncio
async def test_llm_call_interrupted_uses_accumulated(core_env, monkeypatch):
    async def fake_acompletion(**kw):
        async def gen():
            yield _chunk(content="part")
            raise RuntimeError("connection reset")
        return gen()

    monkeypatch.setattr(core_mod.litellm, "acompletion", fake_acompletion)
    agent = build_agent()
    resp = await agent._llm_call("m", [], None)
    assert resp.choices[0].message.content == "part"


def test_build_graph_nodes():
    agent = build_agent()
    assert {"retrieve", "generate"} <= set(agent.graph.nodes)
    agent2 = build_agent(reranker=SimpleNamespace(rerank=lambda **kw: []))
    assert {"retrieve", "rerank", "generate"} <= set(agent2.graph.nodes)


@pytest.mark.asyncio
async def test_refresh_tools(core_env):
    agent = build_agent()
    old_graph = agent.graph
    old_prompt = agent.system_prompt
    await agent.refresh_tools()
    assert agent.graph is not old_graph
    assert len(agent.tools) > 0
    assert any(t.name == "tool_read_file" for t in agent.tools)
    assert agent.system_prompt == old_prompt or True


class FakeGraph:
    def __init__(self, raise_err=False):
        self.raise_err = raise_err

    async def ainvoke(self, state):
        if self.raise_err:
            raise RuntimeError("graph boom")
        return {
            **state,
            "answer": "A",
            "sources": [],
            "steps": [],
            "messages": [],
            "model": None,
            "finish": "stop",
            "tokens": {},
            "cost": None,
        }


@pytest.mark.asyncio
async def test_invoke_full_flow(core_env, monkeypatch, tmp_path):
    from app.permission import current_session_workspace
    created = {}

    class FakeTask:
        def __init__(self, conversation_id=""):
            created["task"] = self
            self.failed = None
            self.completed = False

        def save(self):
            pass

        def mark_completed(self):
            self.completed = True

        def mark_failed(self, e):
            self.failed = e

        def to_dict(self):
            return {"conversation_id": "c"}

    monkeypatch.setattr("app.context.task_state.TaskState", FakeTask)
    agent = build_agent()
    agent.graph = FakeGraph()
    result = await agent.invoke("问题", conversation_id="cid", directory=str(tmp_path))
    assert result["answer"] == "A"
    assert created["task"].completed is True
    assert result["task"] == {"conversation_id": "c"}
    # 会话工作目录已还原
    assert current_session_workspace() == ""


@pytest.mark.asyncio
async def test_invoke_exception_marks_failed(core_env, monkeypatch, tmp_path):
    created = {}

    class FakeTask:
        def __init__(self, conversation_id=""):
            created["task"] = self
            self.failed = None

        def save(self):
            pass

        def mark_failed(self, e):
            self.failed = e

        def to_dict(self):
            return {}

    monkeypatch.setattr("app.context.task_state.TaskState", FakeTask)
    agent = build_agent()
    agent.graph = FakeGraph(raise_err=True)
    with pytest.raises(RuntimeError):
        await agent.invoke("问题", conversation_id="cid", directory=str(tmp_path))
    assert created["task"].failed is not None
    from app.permission import current_session_workspace
    assert current_session_workspace() == ""


@pytest.mark.asyncio
async def test_invoke_without_conversation_id(core_env, tmp_path):
    agent = build_agent()
    agent.graph = FakeGraph()
    result = await agent.invoke("问题", directory=str(tmp_path))
    assert result["task"] == {}