# -*- coding: utf-8 -*-
"""fstools exec/execv/lexcmd 剩余分支用例（补 test_cmd_lex / test_fstools 未覆盖）。

覆盖：
  - lexcmd：_is_redirect_token 正则、_first_command 跳过 env/$/重定向、POSIX
    _extract_redirect_targets、_check_redirect_targets_permission 三分支
  - execv：_split_shell_segments POSIX 分支、_backtick_bodies、_check_single_allowed
    （路径解析/ask 三态）、_validate_shell_command 递归与空命令、_win_cmd_needs_shell、
    _needs_shell POSIX 分支、_check_command_blacklist 各解释器、_ssrf_check_command
  - exec：decode_process_output 三级解码、append_cmd_dialect_hint、_format_execute_output
    截断、_kill_process_tree 双平台、_run_shell 超时、tool_execute 全分支

运行：pytest tests/test_fstools_exec.py
"""
import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

import app.tools.fstools.exec as ex
import app.tools.fstools.execv as ev
import app.tools.fstools.lexcmd as lx
from app.permission import NeedsPermission


# ── lexcmd ─────────────────────────────────────────────────────────────────

def test_is_redirect_token():
    assert lx._is_redirect_token(">") is True
    assert lx._is_redirect_token("2>") is True
    assert lx._is_redirect_token("2>>") is True
    assert lx._is_redirect_token("2>&1") is True
    assert lx._is_redirect_token("1<&2") is True
    assert lx._is_redirect_token("echo") is False
    assert lx._is_redirect_token("") is False


def test_first_command_skips_env_and_redirect():
    assert lx._first_command(["FOO=bar", "echo", "hi"]) == "echo"
    assert lx._first_command(["$", "echo", "hi"]) == "echo"
    assert lx._first_command(["echo", "hi", ">", "out.txt"]) == "echo"
    assert lx._first_command([">", ">>", "2>"]) is None
    assert lx._first_command(["echo"]) == "echo"


def test_extract_redirect_targets_windows(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    assert lx._extract_redirect_targets("echo hi > out.txt 2> err.txt &> both.txt") == [
        "out.txt", "err.txt", "both.txt",
    ]
    # 产品现状：Windows 分支对 < 输入重定向也提取为目标（保守过度检查，锁定）
    assert lx._extract_redirect_targets("cat < in.txt | head") == ["in.txt"]


def test_extract_redirect_targets_posix(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    assert lx._extract_redirect_targets("echo hi > out.txt 2> err.txt") == ["out.txt", "err.txt"]
    assert lx._extract_redirect_targets("echo hi | grep x") == []


def test_check_redirect_permission(tmp_path, monkeypatch):
    def mgr(decision):
        return SimpleNamespace(check=lambda p, op: decision)
    # 无目标 → 直接返回（Windows 分支，默认 os.name=nt）
    lx._check_redirect_targets_permission("echo hi", tmp_path)
    # /dev/null 跳过
    lx._check_redirect_targets_permission("echo hi > /dev/null", tmp_path)
    # ask → NeedsPermission
    monkeypatch.setattr(lx, "get_perm_mgr", lambda: mgr("ask"))
    with pytest.raises(NeedsPermission):
        lx._check_redirect_targets_permission("echo hi > out.txt", tmp_path)
    # deny → PermissionError
    monkeypatch.setattr(lx, "get_perm_mgr", lambda: mgr("deny"))
    with pytest.raises(PermissionError):
        lx._check_redirect_targets_permission("echo hi > out.txt", tmp_path)
    # allow → 不抛
    monkeypatch.setattr(lx, "get_perm_mgr", lambda: mgr("allow"))
    lx._check_redirect_targets_permission("echo hi > out.txt", tmp_path)


# ── execv ──────────────────────────────────────────────────────────────────

def test_split_shell_segments_posix(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    assert ev._split_shell_segments("cat x | evil") == [["cat", "x"], ["evil"]]
    assert ev._split_shell_segments("echo a && echo b > out") == [["echo", "a"], ["echo", "b", ">", "out"]]
    assert ev._split_shell_segments("echo $(whoami)") == [["echo", "$"], ["whoami"]]


def test_backtick_bodies():
    assert ev._backtick_bodies("echo `date +%Y` and `pwd`") == ["date +%Y", "pwd"]
    assert ev._backtick_bodies("no backticks") == []


def test_check_single_allowed_allowed():
    ev._check_single_allowed("python", cwd=".")  # 不抛


def test_check_single_allowed_path_resolution(tmp_path, monkeypatch):
    sub = tmp_path / "bin"
    sub.mkdir()
    exe = sub / "mytool.py"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(ev, "_workspace", lambda: tmp_path)
    ev._check_single_allowed("bin/mytool.py", cwd=str(tmp_path))  # 工作区内可解析 → 放行


def test_check_single_allowed_path_not_in_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "_workspace", lambda: tmp_path)
    with pytest.raises(ValueError):
        ev._check_single_allowed("C:/Windows/System32/cmd.exe", cwd=str(tmp_path))


def test_check_single_allowed_ask_modes(monkeypatch):
    class Mgr:
        def __init__(self, d):
            self.d = d

        def check_command(self, cmd):
            return self.d
    # allow → 放行
    monkeypatch.setattr(ev, "get_perm_mgr", lambda: Mgr("allow"))
    ev._check_single_allowed("customtool", cwd=".", ask=True)
    # deny → ValueError
    monkeypatch.setattr(ev, "get_perm_mgr", lambda: Mgr("deny"))
    with pytest.raises(ValueError):
        ev._check_single_allowed("customtool", cwd=".", ask=True)
    # ask → NeedsPermission
    monkeypatch.setattr(ev, "get_perm_mgr", lambda: Mgr("ask"))
    with pytest.raises(NeedsPermission):
        ev._check_single_allowed("customtool", cwd=".", ask=True)
    # 非 ask → ValueError
    monkeypatch.setattr(ev, "get_perm_mgr", lambda: Mgr("allow"))
    with pytest.raises(ValueError):
        ev._check_single_allowed("customtool", cwd=".")


def test_validate_shell_command_backtick_recursion(monkeypatch):
    # 反引号内命令递归校验：内层 evil 被拦
    with pytest.raises(ValueError):
        ev._validate_shell_command("echo `evilcmd`", cwd=".")
    # 空命令
    with pytest.raises(ValueError):
        ev._validate_shell_command("   ", cwd=".")


def test_win_cmd_needs_shell(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(ev, "_win_which_cache", {})
    assert ev._win_cmd_needs_shell("echo hi") is True  # 内建
    assert ev._win_cmd_needs_shell("C:/x/script.bat") is True  # .bat
    monkeypatch.setattr(ev.shutil, "which", lambda b: "C:/npm/npm.cmd")
    assert ev._win_cmd_needs_shell("npm --version") is True  # which 解析 .cmd
    monkeypatch.setattr(ev.shutil, "which", lambda b: "C:/Python/python.exe")
    assert ev._win_cmd_needs_shell("python --version") is False
    monkeypatch.setattr(ev.shutil, "which", lambda b: None)
    assert ev._win_cmd_needs_shell("ghostcmd") is False


def test_needs_shell_posix(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    assert ev._needs_shell("echo hi") is False
    assert ev._needs_shell("ls | grep x") is True
    assert ev._needs_shell("echo $HOME") is True
    assert ev._needs_shell("cat file > out") is True
    assert ev._needs_shell("echo 'quoted | pipe'") is False


def test_needs_shell_windows(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    assert ev._needs_shell("dir") is True  # cmd 内建
    assert ev._needs_shell("copy a.txt b.txt") is True  # 内建
    assert ev._needs_shell("echo %PATH%") is True  # %VAR%
    assert ev._needs_shell("git log | head") is True  # 管道


def test_check_command_blacklist_python_c():
    with pytest.raises(ValueError):
        ev._check_command_blacklist('python -c "import os; os.system(\'whoami\')"')
    with pytest.raises(ValueError):
        ev._check_command_blacklist('python -c "import socket; socket.connect((1,2))"')
    # 危险模式不存在 → 放行
    ev._check_command_blacklist('python -c "print(1+1)"')
    # 非解释器 → 直接返回
    ev._check_command_blacklist("git status")


def test_check_command_blacklist_other_interpreters():
    with pytest.raises(ValueError):
        ev._check_command_blacklist('node -e "eval(1)"')
    with pytest.raises(ValueError):
        ev._check_command_blacklist('powershell -Command "Invoke-Expression x"')
    with pytest.raises(ValueError):
        ev._check_command_blacklist('cmd /c "whoami"')
    ev._check_command_blacklist('python script.py')  # 无 -c flag → 不检查


def test_ssrf_check_command(monkeypatch):
    called = []
    monkeypatch.setattr("app.utils.ssrf.check_url", lambda u: called.append(u))
    monkeypatch.setattr("app.utils.ssrf.allow_internal", lambda: False)
    monkeypatch.setattr("app.utils.ssrf._host_is_internal", lambda h: h == "10.0.0.1")
    # 非网络命令 → 不检查
    ev._ssrf_check_command("echo hi")
    # URL 目标 → check_url
    ev._ssrf_check_command("curl https://example.com/x")
    assert called == ["https://example.com/x"]
    # 裸内网目标 → ValueError
    with pytest.raises(ValueError):
        ev._ssrf_check_command("curl 10.0.0.1")
    # 标志位 / user@host 解析
    ev._ssrf_check_command("curl -s -o /dev/null https://a.b")
    with pytest.raises(ValueError):
        ev._ssrf_check_command("ssh user@10.0.0.1:/path")
    # allow_internal → 放行
    monkeypatch.setattr("app.utils.ssrf.allow_internal", lambda: True)
    ev._ssrf_check_command("curl 10.0.0.1")  # 不抛


# ── exec ───────────────────────────────────────────────────────────────────

def test_decode_process_output():
    assert ex.decode_process_output(b"") == ""
    assert ex.decode_process_output("你好".encode("utf-8")) == "你好"
    # GBK 字节 → GBK 解码
    gbk = "中文".encode("gbk")
    assert ex.decode_process_output(gbk) == "中文"
    # 两者都失败 → replace
    bad = b"\xff\xfe\x00\x81\x82"
    out = ex.decode_process_output(bad)
    assert "\ufffd" in out or out != ""


def test_append_cmd_dialect_hint(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    assert ex.append_cmd_dialect_hint("") == ""
    assert ex.append_cmd_dialect_hint("ok output") == "ok output"
    assert "cmd.exe hint" in ex.append_cmd_dialect_hint("'foo' 不是内部或外部命令")
    assert "cmd.exe hint" in ex.append_cmd_dialect_hint("echo $?")
    assert "cmd.exe hint" in ex.append_cmd_dialect_hint("is not recognized as an internal or external command")
    monkeypatch.setattr(os, "name", "posix")
    assert ex.append_cmd_dialect_hint("不是内部或外部命令") == "不是内部或外部命令"


def test_format_execute_output(monkeypatch):
    out = ex._format_execute_output(0, "stdout", "stderr", "cmd")
    assert out["metadata"]["exit_code"] == 0
    assert "stdout" in out["output"] and "[stderr]" in out["output"]
    # 无输出 → 仅 header
    out2 = ex._format_execute_output(0, "", "")
    assert out2["output"] == "Exit code: 0"


def test_format_execute_output_truncated(monkeypatch):
    monkeypatch.setattr(ex, "MAX_EXECUTE_OUTPUT_LENGTH", 50)
    monkeypatch.setattr("app.context.tool_output._write_truncated", lambda s: "data/truncation/x.txt")
    out = ex._format_execute_output(0, "x" * 100, "", "c")
    assert out["metadata"]["truncated"] is True
    assert "Output truncated; full output saved to" in out["output"]
    # 截断写盘失败 → 通用提示
    monkeypatch.setattr("app.context.tool_output._write_truncated", lambda s: (_ for _ in ()).throw(OSError()))
    out2 = ex._format_execute_output(0, "x" * 100, "", "c")
    assert "truncated output as it exceeded" in out2["output"]


def test_kill_process_tree_win(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    runs = []
    monkeypatch.setattr(ex.subprocess, "run", lambda *a, **k: runs.append(a) or SimpleNamespace())
    ex._kill_process_tree(SimpleNamespace(pid=123))
    assert "taskkill" in runs[0][0]


def test_kill_process_tree_posix(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(ex.signal, "SIGKILL", 9, raising=False)  # Windows 无 SIGKILL
    killed = []
    monkeypatch.setattr(os, "getpgid", lambda pid: 99, raising=False)
    monkeypatch.setattr(os, "killpg", lambda *a: killed.append(a), raising=False)
    ex._kill_process_tree(SimpleNamespace(pid=1))
    assert killed and killed[0][0] == 99


def test_run_shell_success(monkeypatch):
    proc = SimpleNamespace(returncode=0)
    monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(ex, "decode_process_output", lambda b: "OUT")
    monkeypatch.setattr(proc, "communicate", lambda timeout: (b"o", b"e"), raising=False)
    rc, out, err = ex._run_shell("echo hi", "/cwd", 30)
    assert (rc, out, err) == (0, "OUT", "OUT")


def test_run_shell_timeout(monkeypatch):
    proc = SimpleNamespace(returncode=0)
    killed = []
    monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: proc)

    def communicate(timeout=None):
        if timeout is not None:
            raise ex.subprocess.TimeoutExpired("cmd", timeout)
        return (b"o", b"e")
    monkeypatch.setattr(proc, "communicate", communicate, raising=False)
    monkeypatch.setattr(ex, "_kill_process_tree", lambda p: killed.append(p))
    monkeypatch.setattr(ex, "decode_process_output", lambda b: b.decode())
    with pytest.raises(ex.subprocess.TimeoutExpired):
        ex._run_shell("sleep 100", "/cwd", 1)
    assert killed


def _tool_execute_env(monkeypatch, tmp_path, **over):
    p = tmp_path
    monkeypatch.setattr("app.tools.fstools.exec._resolve", lambda wd: p)
    monkeypatch.setattr(ex, "get_perm_mgr", lambda: SimpleNamespace(check=lambda path, op: over.get("decision", "allow")))
    monkeypatch.setattr(ex, "_check_redirect_targets_permission", lambda c, w: None)
    return p


@pytest.mark.asyncio
async def test_tool_execute_validation_error(monkeypatch, tmp_path):
    _tool_execute_env(monkeypatch, tmp_path)
    monkeypatch.setattr(ex, "_validate_shell_command", lambda c, cwd, ask: (_ for _ in ()).throw(ValueError("bad cmd")))
    out = ex.tool_execute("evil")
    assert out["metadata"].get("error") is True
    assert "bad cmd" in out["output"]


@pytest.mark.asyncio
async def test_tool_execute_deny(monkeypatch, tmp_path):
    _tool_execute_env(monkeypatch, tmp_path, decision="deny")
    monkeypatch.setattr(ex, "_validate_shell_command", lambda c, cwd, ask: None)
    out = ex.tool_execute("echo hi")
    assert "access denied" in out["output"]


@pytest.mark.asyncio
async def test_tool_execute_ask_raises(monkeypatch, tmp_path):
    _tool_execute_env(monkeypatch, tmp_path, decision="ask")
    monkeypatch.setattr(ex, "_validate_shell_command", lambda c, cwd, ask: None)
    with pytest.raises(NeedsPermission):
        ex.tool_execute("echo hi")


@pytest.mark.asyncio
async def test_tool_execute_shell_path(monkeypatch, tmp_path):
    _tool_execute_env(monkeypatch, tmp_path)
    monkeypatch.setattr(ex, "_validate_shell_command", lambda c, cwd, ask: None)
    monkeypatch.setattr(ex, "_needs_shell", lambda c: True)
    monkeypatch.setattr(ex, "_run_shell", lambda c, wd, t: (0, "out", ""))
    monkeypatch.setattr(os, "name", "posix")
    out = ex.tool_execute("echo hi | grep hi")
    assert out["metadata"]["exit_code"] == 0
    assert "out" in out["output"]


@pytest.mark.asyncio
async def test_tool_execute_non_shell_path(monkeypatch, tmp_path):
    _tool_execute_env(monkeypatch, tmp_path)
    monkeypatch.setattr(ex, "_validate_shell_command", lambda c, cwd, ask: None)
    monkeypatch.setattr(ex, "_needs_shell", lambda c: False)
    monkeypatch.setattr(ex.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="RES", stderr=""))
    monkeypatch.setattr(os, "name", "posix")
    out = ex.tool_execute("python script.py")
    assert "RES" in out["output"]


@pytest.mark.asyncio
async def test_tool_execute_timeout(monkeypatch, tmp_path):
    _tool_execute_env(monkeypatch, tmp_path)
    monkeypatch.setattr(ex, "_validate_shell_command", lambda c, cwd, ask: None)
    monkeypatch.setattr(ex, "_needs_shell", lambda c: False)
    monkeypatch.setattr(ex.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(ex.subprocess.TimeoutExpired("c", 5)))
    out = ex.tool_execute("sleep 100")
    assert out["metadata"].get("timed_out") is True


@pytest.mark.asyncio
async def test_tool_execute_generic_error(monkeypatch, tmp_path):
    _tool_execute_env(monkeypatch, tmp_path)
    monkeypatch.setattr(ex, "_validate_shell_command", lambda c, cwd, ask: None)
    monkeypatch.setattr(ex, "_needs_shell", lambda c: False)
    monkeypatch.setattr(ex.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = ex.tool_execute("echo hi")
    assert "Error executing command: boom" in out["output"]