# -*- coding: utf-8 -*-
"""CodeAct 受控执行器测试（设计 docs/codeact-sandbox-design.md v2.2）。

覆盖：
  - ast_guard.analyze_code：白名单导入 / 黑名单名 / dunder 属性 / from-* /
    超长 / 超节点 / 语法错误 / 空代码
  - result：帧编解码往返、超帧拒绝、CodeActResult.to_text 分节与截断
  - session：AST 守卫接线（拒绝不进沙盒）、并发信号量读取配置并缓存
  - execv §4.0：CODEACT_ENABLED 时 python -c / node -e 收紧，shell/脚本文件不受影响
  - agent.tools：系统提示词仅在 CodeAct 开启时含 tool_code_exec 段
  - job_object：非 win32 返回 None；resume_thread 已移除（v2.2 assign-after-spawn）

运行：pytest tests/test_codeact.py
"""
import json
import os
import sys
import threading
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

import app.codeact.job_object as jo
import app.codeact.session as cs
from app.codeact import result as rs
from app.codeact.ast_guard import analyze_code
from app.config import settings
import app.tools.fstools.execv as ev


# ── ast_guard ──────────────────────────────────────────────────────────────

def test_ast_guard_allows_safe_code():
    assert analyze_code("import math\nx = math.sqrt(16)\nprint(x)") is None
    assert analyze_code("from collections import Counter\nc = Counter('aab')") is None


def test_ast_guard_blocks_non_whitelisted_import():
    err = analyze_code("import os\nx = os.getcwd()")
    assert err and "os" in err
    assert analyze_code("import socket") is not None


def test_ast_guard_blocks_from_import_star_and_blacklisted_name():
    assert analyze_code("from math import *") is not None
    assert analyze_code("from math import open") is not None


def test_ast_guard_blocks_blacklisted_builtin():
    assert analyze_code("open('/etc/passwd')") is not None
    assert analyze_code("eval('1')") is not None
    assert analyze_code("globals()") is not None


def test_ast_guard_blocks_dunder_attribute():
    assert analyze_code("x = ().__class__") is not None


def test_ast_guard_empty_and_whitespace():
    assert analyze_code("") is not None
    assert analyze_code("   \n  ") is not None


def test_ast_guard_max_chars():
    assert analyze_code("x=1\n" * 100, max_code_chars=10) is not None


def test_ast_guard_max_nodes():
    code = "x = " + " + ".join(["1"] * 100)
    assert analyze_code(code, max_ast_nodes=10) is not None


def test_ast_guard_syntax_error():
    assert analyze_code("def (") is not None


# ── result：帧编解码 + 回灌格式 ──────────────────────────────────────────────

def test_frame_roundtrip():
    payload = {"ch": "result", "ok": True, "stdout": "你好", "n": 1}
    assert rs.decode_frame(rs.encode_frame(payload)) == payload


def test_decode_frame_too_short():
    with pytest.raises(rs.FrameCodecError):
        rs.decode_frame(b"\x00")


def test_decode_frame_incomplete_body():
    data = rs.encode_frame({"a": 1})
    with pytest.raises(rs.FrameCodecError):
        rs.decode_frame(data[:-1])


def test_decode_frame_bad_json():
    import struct
    body = b"{not json"
    with pytest.raises(rs.FrameCodecError):
        rs.decode_frame(struct.pack(">I", len(body)) + body)


def test_encode_frame_oversize():
    with pytest.raises(rs.FrameCodecError):
        rs.encode_frame({"x": "a" * (rs._MAX_FRAME_BYTES + 10)})


def test_result_to_text_sections():
    r = rs.CodeActResult(
        ok=True, stdout="out", stderr="err", last_expr="42",
        files=["a.txt"], duration_ms=1.25,
    )
    txt = r.to_text(max_output_chars=100)
    assert "执行成功" in txt
    assert "--- last expression ---" in txt
    assert "--- stdout ---" in txt and "out" in txt
    assert "--- stderr ---" in txt and "err" in txt
    assert "--- files ---" in txt and "a.txt" in txt


def test_result_to_text_truncates_large_output():
    r = rs.CodeActResult(ok=False, stdout="x" * 1000)
    txt = r.to_text(max_output_chars=50)
    assert "omitted" in txt


def test_result_from_frame_error_code():
    r = rs.CodeActResult.from_frame(
        {"ok": False, "error": {"code": "memory_limit", "msg": "m"}}
    )
    assert r.ok is False
    assert "memory_limit" in r.to_text()


# ── session：AST 守卫接线 + 并发信号量 ──────────────────────────────────────

@pytest.fixture
def codeact_tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "global_paths", lambda: {"data": str(tmp_path / "data")})
    return tmp_path


async def test_session_run_ast_reject_does_not_spawn(codeact_tmp_data, monkeypatch):
    spawned = {"v": False}
    monkeypatch.setattr(cs.ExecutionSession, "_spawn", lambda self: spawned.__setitem__("v", True))
    sess = cs.ExecutionSession("import os", codeact_tmp_data)
    res = await sess.run()
    assert res.ok is False
    assert res.error["code"] == "ast_rejected"
    assert spawned["v"] is False


def test_semaphore_uses_configured_concurrency(monkeypatch):
    monkeypatch.setattr(cs, "_codeact_semaphore", None)
    monkeypatch.setattr(settings, "codeact_max_concurrent", 3)
    sem = cs._get_semaphore()
    assert sem._value == 3
    assert cs._get_semaphore() is sem  # 缓存复用


def test_semaphore_minimum_one(monkeypatch):
    monkeypatch.setattr(cs, "_codeact_semaphore", None)
    monkeypatch.setattr(settings, "codeact_max_concurrent", 0)
    assert cs._get_semaphore()._value == 1


# ── execv §4.0：内联代码入口收紧 ────────────────────────────────────────────

@pytest.fixture
def codeact_off(monkeypatch):
    monkeypatch.setattr(settings, "codeact_enabled", False)


@pytest.fixture
def codeact_on(monkeypatch):
    monkeypatch.setattr(settings, "codeact_enabled", True)


def test_codeact_off_allows_benign_python_c(codeact_off):
    ev._validate_shell_command('python -c "print(1+1)"', cwd=".")  # 不抛


def test_codeact_on_blocks_python_c(codeact_on):
    with pytest.raises(ValueError, match="tool_code_exec"):
        ev._validate_shell_command('python -c "print(1+1)"', cwd=".")


def test_codeact_on_blocks_node_e(codeact_on):
    with pytest.raises(ValueError):
        ev._validate_shell_command('node -e "console.log(1)"', cwd=".")


def test_codeact_on_blocks_inline_in_compound_command(codeact_on):
    with pytest.raises(ValueError):
        ev._validate_shell_command('echo hi && python -c "print(1)"', cwd=".")


def test_codeact_on_allows_script_file(codeact_on):
    ev._validate_shell_command("python script.py", cwd=".")  # 无 -c → 不收紧
    ev._validate_shell_command("node app.js", cwd=".")


def test_codeact_inline_powershell_and_cmd_not_gated(codeact_on):
    # 收紧只针对 python/node 内联；powershell -Command / cmd /c 仍走 tool_execute
    ev._check_codeact_inline('powershell -Command "Get-Date"')
    ev._check_codeact_inline("cmd /c dir")


def test_interpreter_inline_flag_detection():
    assert ev._interpreter_inline_flag('python -c "x"') == ("python", "-c")
    assert ev._interpreter_inline_flag('node -e "x"') == ("node", "-e")
    assert ev._interpreter_inline_flag('powershell -Command "x"') == ("powershell", "-Command")
    assert ev._interpreter_inline_flag("git status") is None


# ── 系统提示词（§10）────────────────────────────────────────────────────────

class _FakeLoader:
    def get_enabled_skills(self):
        return []

    def get_enabled_plugins(self):
        return []


def test_prompt_includes_codeact_when_enabled(monkeypatch):
    from app.agent.tools import build_system_prompt_no_kb
    monkeypatch.setattr(settings, "codeact_enabled", True)
    prompt = build_system_prompt_no_kb(_FakeLoader(), _FakeLoader())
    assert "tool_code_exec" in prompt


def test_prompt_excludes_codeact_when_disabled(monkeypatch):
    from app.agent.tools import build_system_prompt_no_kb
    monkeypatch.setattr(settings, "codeact_enabled", False)
    prompt = build_system_prompt_no_kb(_FakeLoader(), _FakeLoader())
    assert "tool_code_exec" not in prompt


# ── job_object（v2.2 assign-after-spawn）────────────────────────────────────

def test_job_object_non_windows_returns_none(monkeypatch):
    monkeypatch.setattr(jo.os, "name", "posix")
    assert jo.JobObject.create(64, 4) is None


def test_job_object_resume_thread_removed():
    assert not hasattr(jo, "resume_thread")


# ── §10：连续失败达上限停用 tool_code_exec（generate.py/_build_tool_defs 强制）──────────

def _codeact_stub_agent(conv: str, failures: int):
    """用 object.__new__ 绕过 __init__（不触碰 Retriever/依赖），只装 _build_tool_defs
    需要的属性和会话工具缓存，验证「失败达上限 → schema 摘除」的真实分支。"""
    from app.agent.tools import ToolDef
    from app.agent.graphmod.base import RAGAgentBase
    stub = object.__new__(RAGAgentBase)
    stub.model = "deepseek/deepseek-v4-flash"
    stub.tools = [
        ToolDef("tool_code_exec", "受控代码执行", {"type": "object", "properties": {}}, lambda: ""),
        ToolDef("tool_read_file", "读取文件", {"type": "object", "properties": {}}, lambda: ""),
        ToolDef("tool_ls", "列出目录", {"type": "object", "properties": {}}, lambda: ""),
    ]
    stub._codeact_failures = {conv: failures}
    stub._conv_tool_cache = {}
    stub._conv_tool_cache_lock = threading.Lock()
    stub._conv_tool_cache_max = 200
    stub._pinned_tool_names = lambda: set()
    return stub


def test_codeact_tool_dropped_at_failure_limit(monkeypatch):
    monkeypatch.setattr(settings, "codeact_enabled", True)
    monkeypatch.setattr(settings, "codeact_max_retries", 3)
    stub = _codeact_stub_agent("conv1", failures=3)
    defs = stub._build_tool_defs("hi", conversation_id="conv1", model="deepseek/deepseek-v4-flash")
    names = [d["function"]["name"] for d in defs]
    assert "tool_code_exec" not in names
    assert "tool_read_file" in names and "tool_ls" in names  # 其余工具照常


def test_codeact_tool_kept_below_limit(monkeypatch):
    monkeypatch.setattr(settings, "codeact_enabled", True)
    monkeypatch.setattr(settings, "codeact_max_retries", 3)
    stub = _codeact_stub_agent("conv2", failures=2)
    defs = stub._build_tool_defs("hi", conversation_id="conv2", model="deepseek/deepseek-v4-flash")
    names = [d["function"]["name"] for d in defs]
    assert "tool_code_exec" in names


def test_codeact_disabled_prompt_constant():
    from app.agent.graphmod.constants import CODEACT_DISABLED_PROMPT
    assert "tool_code_exec" in CODEACT_DISABLED_PROMPT
    assert "连续失败" in CODEACT_DISABLED_PROMPT
