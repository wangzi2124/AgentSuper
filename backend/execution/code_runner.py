"""多语言代码执行（对齐 agent.md §5.2 / §9 execution/code_runner.py）。

在沙箱中运行模型生成的**整段代码**（区别于 `sandbox.shell_exec` 跑任意命令行）：
- 白名单语言 → 固定解释器 + 临时源文件，不经 shell，避免参数注入；
- 危险模式拦截 + 超时 + 有界输出（复用 `sandbox` / `filesystem` 的能力）；
- 运行时缺失（如未装 node）时返回可读错误，而非抛异常。

生产应替换为容器 / gVisor 隔离（§11.2、M4）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .filesystem import bounded
from .sandbox import MAX_TIMEOUT, BLOCKED_PATTERNS

DEFAULT_TIMEOUT = 30
DEFAULT_OUTPUT_CHARS = 10_000


@dataclass(frozen=True)
class Language:
    name: str
    ext: str
    # 解释器候选：按顺序探测 which()，第一个可用者胜出
    interpreters: tuple[str, ...]
    # 命令行模板：{python} / {file} 占位
    template: tuple[str, ...] = ("{interpreter}", "{file}")


LANGUAGES: dict[str, Language] = {
    "python": Language("python", ".py", (sys.executable, "python3", "python")),
    "python3": Language("python", ".py", (sys.executable, "python3", "python")),
    "javascript": Language("javascript", ".js", ("node",)),
    "node": Language("javascript", ".js", ("node",)),
    "js": Language("javascript", ".js", ("node",)),
    "typescript": Language("typescript", ".ts", ("tsx", "deno"), ("{interpreter}", "run", "{file}")),
    "ts": Language("typescript", ".ts", ("tsx", "deno"), ("{interpreter}", "run", "{file}")),
    "bash": Language("bash", ".sh", ("bash",)),
    "sh": Language("bash", ".sh", ("bash", "sh")),
    "shell": Language("bash", ".sh", ("bash", "sh")),
}

ALIASES = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "sh": "bash",
}


def resolve_language(language: str) -> Language | None:
    """把别名/大小写归一为 Language，不支持时返回 None。"""
    key = (language or "").strip().lower()
    key = ALIASES.get(key, key)
    return LANGUAGES.get(key)


def supported_languages() -> list[str]:
    return sorted({lang.name for lang in LANGUAGES.values()})


def _find_interpreter(candidates: Sequence[str]) -> str | None:
    for cand in candidates:
        if Path(cand).is_absolute() and Path(cand).exists():
            return cand
        found = shutil.which(cand)
        if found:
            return found
    return None


def _blocked(code: str) -> str | None:
    for pat in BLOCKED_PATTERNS:
        if pat in code:
            return pat
    return None


def run_code(
    code: str,
    language: str = "python",
    *,
    workdir: str = ".",
    timeout: int = DEFAULT_TIMEOUT,
    stdin: str | None = None,
    max_output: int = DEFAULT_OUTPUT_CHARS,
) -> str:
    """运行整段代码，返回 stdout+stderr（头尾有界），或 "❌ ..." 错误说明。

    Args:
        code: 完整源码。
        language: 语言名（python/javascript/typescript/bash，支持 py/js/ts/sh 别名）。
        workdir: 工作目录（须存在）。
        timeout: 秒，1..MAX_TIMEOUT。
        stdin: 可选标准输入。
        max_output: 输出字符上限。
    """
    lang = resolve_language(language)
    if lang is None:
        return f"❌ 不支持的语言：{language}（可用：{', '.join(supported_languages())}）"

    pat = _blocked(code)
    if pat:
        return f"❌ 代码被安全策略拒绝：包含危险模式 '{pat}'"

    cwd = Path(workdir)
    if not cwd.is_dir():
        return f"❌ 工作目录不存在：{workdir}"

    interpreter = _find_interpreter(lang.interpreters)
    if interpreter is None:
        return f"❌ 未安装 {lang.name} 运行时（已尝试：{', '.join(lang.interpreters)}）"

    timeout = min(max(int(timeout), 1), MAX_TIMEOUT)
    tmp_dir = Path(tempfile.mkdtemp(prefix="coderun_"))
    src = tmp_dir / f"main{lang.ext}"
    try:
        src.write_text(code, encoding="utf-8")
        cmd = [str(interpreter) if part == "{interpreter}" else src.as_posix() if part == "{file}" else part for part in lang.template]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(cwd),
                input=stdin,
            )
        except subprocess.TimeoutExpired:
            return f"❌ 代码执行超时（{timeout}s）"
        except Exception as e:  # noqa: BLE001
            detail = str(e) or repr(e)
            return f"❌ 执行失败：{detail}"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        output = f"[exit {result.returncode}]\n{output}".strip()
    return bounded(output, max_output) or "(无输出)"


class CodeRunner:
    """可注入执行器的代码运行器（便于测试替换 `executor`）。"""

    def __init__(
        self,
        *,
        executor: Callable[..., str] | None = None,
        default_timeout: int = DEFAULT_TIMEOUT,
        max_output: int = DEFAULT_OUTPUT_CHARS,
    ) -> None:
        self._executor = executor or run_code
        self.default_timeout = default_timeout
        self.max_output = max_output

    def run(
        self,
        code: str,
        language: str = "python",
        *,
        workdir: str = ".",
        timeout: int | None = None,
        stdin: str | None = None,
    ) -> str:
        return self._executor(
            code,
            language,
            workdir=workdir,
            timeout=timeout if timeout is not None else self.default_timeout,
            stdin=stdin,
            max_output=self.max_output,
        )

    def run_code(self, code: str, language: str = "python", *, workdir: str = ".") -> str:
        """工具入口：运行代码片段。

        Args:
            code: 完整源码。
            language: 语言名（python/javascript/typescript/bash）。
            workdir: 工作目录。
        """
        return self.run(code, language, workdir=workdir)
