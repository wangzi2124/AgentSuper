"""拆分模块 `exec`（含 MAX_EXECUTE_OUTPUT_LENGTH、_CMD_DIALECT_HINT、_format_execute_output、_kill_process_tree、_run_shell、append_cmd_dialect_hint、decode_process_output、tool_execute）。

原文件 docstring: (无)"""

# ── 复制自原模块的顶层 import ──

import base64

import json

import os

import re

import shlex

import shutil

import signal

import stat

import subprocess

import time

from datetime import datetime

from pathlib import Path

from typing import Optional

from app.filesystem import GitignoreMatcher, ScanCache, get_project, glob_to_regex

from app.permission import get_manager as get_perm_mgr, NeedsPermission, current_session_workspace

# ── 跨子模块依赖（自动生成）──

from .common import _coerce_int
from .common import _env
from .execv import _needs_shell
from .execv import _validate_shell_command
from .lexcmd import _check_redirect_targets_permission
from .workspace import _resolve

# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──

MAX_EXECUTE_OUTPUT_LENGTH = 30_000

def _kill_process_tree(proc: subprocess.Popen) -> None:
    """杀掉进程及其整个后代进程树（Windows 用 taskkill /T /F，POSIX 用 killpg）。"""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/pid", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

def decode_process_output(data: bytes) -> str:
    """子进程输出三级解码：utf-8 严格 → GBK → utf-8 replace。

    Windows 控制台程序（cmd.exe 内建报错、git 中文提示等）输出本地代码页
    （中文系统 GBK），而 node/npm 等现代工具链输出 UTF-8 —— 单一编码必然
    弄错一边。utf-8 严格解码失败再试 GBK，都失败才降级替换符。
    """
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("gbk")
    except UnicodeDecodeError:
        pass
    return data.decode("utf-8", errors="replace")

def _run_shell(command: str, resolved_cwd: str, timeout: int) -> tuple[int, str, str]:
    """通过真实 shell 执行命令（Windows: cmd.exe；POSIX: /bin/sh）。

    进程放入新会话/进程组，超时时杀掉整个进程树（opencode killTree 语义）。
    管道按字节读取 + 三级解码（见 decode_process_output），不依赖 -X utf8/locale。
    """
    popen_kwargs: dict = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        command, shell=True,
        cwd=resolved_cwd, **popen_kwargs,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, decode_process_output(stdout or b""), decode_process_output(stderr or b"")
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        stdout, stderr = proc.communicate()
        raise subprocess.TimeoutExpired(
            command, timeout,
            decode_process_output(stdout or b""), decode_process_output(stderr or b""),
        )

_CMD_DIALECT_HINT = (
    "[cmd.exe hint] Commands here run via cmd.exe on Windows, not bash: use backslash paths "
    "(.venv\\Scripts\\python.exe — forward slashes fail), %ERRORLEVEL% instead of $?, "
    "and & / && instead of ; as command separator."
)

def append_cmd_dialect_hint(text: str) -> str:
    """检测 POSIX 方言在 cmd.exe 下的典型失败签名，附一行修正指引给 LLM 自纠。

    opencode 无此问题（Bun 跨平台 shell 天然吃 POSIX 语法）；本实现用真 cmd.exe，
    而 LLM 训练语料偏 bash —— 以前失败反馈是哑弹（NotImplementedError），模型永远
    学不到语法错了；现在把真实方言错误 + 修正指引一起回传，下一轮即可自纠。
    """
    if os.name != "nt" or not text:
        return text
    posix_signature = (
        "不是内部或外部命令" in text
        or "is not recognized as an internal or external command" in text
        or "$?" in text
    )
    if not posix_signature:
        return text
    return f"{text}\n{_CMD_DIALECT_HINT}"

def _format_execute_output(rc: int, stdout: str, stderr: str, command: str = "") -> dict:
    parts = []
    if stdout:
        parts.append(stdout.rstrip())
    if stderr:
        parts.append(f"[stderr]\n{stderr.rstrip()}")
    output = "\n".join(parts)
    header = f"Exit code: {rc}"
    truncated = len(output) > MAX_EXECUTE_OUTPUT_LENGTH
    preview = output
    output_path = None
    if truncated:
        # 对齐 opencode shell.ts：完整输出写入 truncation 文件，只回传预览 + 提示
        try:
            from app.context.tool_output import _write_truncated
            output_path = _write_truncated(output)
            preview = output[:MAX_EXECUTE_OUTPUT_LENGTH]
            hint = f"\n\n<bash_metadata>\nOutput truncated; full output saved to: {output_path}\n</bash_metadata>"
        except Exception:
            hint = "\n\n<bash_metadata>\nbash tool truncated output as it exceeded 30000 char limit\n</bash_metadata>"
        body = preview + hint
    else:
        body = output
    if body:
        text = f"{header}\n{body}"
    else:
        text = header
    text = append_cmd_dialect_hint(text)
    return _env(
        "execute",
        text,
        exit_code=rc,
        truncated=truncated,
        output_path=output_path,
        command=command[:200],
    )

def tool_execute(command: str, timeout: int = 300, workdir: str = ".") -> dict:
    """执行shell命令并返回标准输出、标准错误和退出码。

    支持管道/重定向/&& 等真实 shell 语义；安全校验（白名单/黑名单/SSRF）逐段生效，
    防止 `cat x | evil` 绕过首 token 白名单。无 shell 语义时保持原 exec 路径。
    超长输出（>30KB）截断为预览，完整输出写入 data/truncation/tool_execute_*.txt 并提示路径。
    """
    timeout = _coerce_int(timeout, 300)
    if timeout > 600:
        timeout = 600
    if timeout < 1:
        timeout = 5
    resolved_cwd = _resolve(workdir)
    try:
        _validate_shell_command(command, cwd=resolved_cwd, ask=True)
    except ValueError as e:
        return _env("execute", f"Error: {e}", error=True)
    mgr = get_perm_mgr()
    decision = mgr.check(str(resolved_cwd), "execute")
    if decision == "ask":
        raise NeedsPermission(str(resolved_cwd), "execute", "tool_execute", {"command": command, "timeout": timeout, "workdir": workdir})
    if decision == "deny":
        return _env("execute", f"Error: access denied to directory '{workdir}'", error=True)
    # 检查写重定向目标文件权限（>、>> 等），防止 shell 命令绕过文件工具的权限检查
    try:
        _check_redirect_targets_permission(command, resolved_cwd)
    except (NeedsPermission, PermissionError):
        raise
    except Exception:
        pass  # 解析失败不阻塞执行（如复杂的动态重定向）
    try:
        if _needs_shell(command):
            rc, stdout, stderr = _run_shell(command, str(resolved_cwd), timeout)
            return _format_execute_output(rc, stdout, stderr, command)
        # Parse command into argument list to avoid shell injection
        args = shlex.split(command)
        result = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(resolved_cwd),
        )
        return _format_execute_output(result.returncode, result.stdout, result.stderr, command)
    except subprocess.TimeoutExpired:
        return _env("execute", f"Error: command timed out after {timeout}s", error=True, timed_out=True)
    except ValueError as e:
        return _env("execute", f"Error: {e}", error=True)
    except Exception as e:
        detail = str(e) or repr(e) or type(e).__name__
        return _env("execute", f"Error executing command: {detail}", error=True)



__all__ = ["MAX_EXECUTE_OUTPUT_LENGTH", "_CMD_DIALECT_HINT", "_format_execute_output", "_kill_process_tree", "_run_shell", "append_cmd_dialect_hint", "decode_process_output", "tool_execute"]
