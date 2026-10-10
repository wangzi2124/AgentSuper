"""进程树终止（设计 §5.2）。

优先 Job Object（session 持有时由 session 调 terminate）；本模块是独立 fallback：
Windows `taskkill /PID <pid> /T /F`，POSIX process-group kill。任何异常都吞掉——
杀树失败不应掩盖真正的超时/错误结果。
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess

logger = logging.getLogger(__name__)


def kill_tree(pid: int, timeout: float = 5.0) -> None:
    """终止 pid 及其整棵子进程树（尽力而为，绝不抛异常）。"""
    if not pid or pid <= 0:
        return
    try:
        if os.name == "nt":
            _kill_tree_windows(pid)
        else:
            _kill_tree_posix(pid)
    except Exception as e:  # noqa: BLE001
        logger.debug("codeact: kill_tree(%s) failed: %s", pid, e)


def _kill_tree_windows(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )


def _kill_tree_posix(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except ProcessLookupError:
        return
    except Exception:  # noqa: BLE001
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass