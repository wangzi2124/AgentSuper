"""沙箱执行（对齐 agent.md §5.2）。

最小实现：危险模式拦截 + 超时 + 有界输出。
生产需替换为容器 / gVisor 隔离（见 §11.2、M4）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .filesystem import bounded

BLOCKED_PATTERNS = [
    "rm -rf /",
    ":(){ :|:& };:",
    "mkfs",
    "dd if=/dev/zero",
    "> /dev/sda",
    "chmod -R 777 /",
]

MAX_TIMEOUT = 120
DEFAULT_OUTPUT_CHARS = 10_000


def shell_exec(
    command: str,
    timeout: int = 30,
    workdir: str = "./workspace",
) -> str:
    """在沙箱中执行 Shell 命令。

    何时使用：需要运行构建/test/脚本且命令自包含时。
    何时不要使用：涉及删除/系统级危险操作；或可用更具体的文件工具完成时。
    参数：
      - command: 完整命令行（支持管道与重定向）
      - timeout: 秒，1..120，默认 30
      - workdir: 工作目录，须存在
    返回：命令输出（stdout+stderr，头尾截断），或 "❌ ..." 开头的错误说明。
    """
    for pat in BLOCKED_PATTERNS:
        if pat in command:
            return f"❌ 命令被安全策略拒绝：包含危险模式 '{pat}'"

    timeout = min(max(int(timeout), 1), MAX_TIMEOUT)
    cwd = Path(workdir)
    if not cwd.is_dir():
        return f"❌ 工作目录不存在：{workdir}"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
        )
    except subprocess.TimeoutExpired:
        return f"❌ 命令执行超时（{timeout}s）"
    except Exception as e:  # noqa: BLE001
        detail = str(e) or repr(e)
        return f"❌ 执行失败：{detail}"

    output = (result.stdout or "") + (result.stderr or "")
    output = output.strip()
    return bounded(output, DEFAULT_OUTPUT_CHARS) or "(无输出)"
