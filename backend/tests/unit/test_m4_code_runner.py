"""L1 单元测试：多语言代码执行器（agent.md §5.2 / §9 code_runner.py）。"""

import sys

import pytest

from execution.code_runner import (
    CodeRunner,
    resolve_language,
    run_code,
    supported_languages,
)


def test_supported_languages_contain_python():
    assert "python" in supported_languages()


def test_resolve_language_alias_and_case():
    assert resolve_language("py").name == "python"
    assert resolve_language("JS").name == "javascript"
    assert resolve_language("cobol") is None


def test_run_python_prints_output(tmp_path):
    out = run_code("print(6*7)", "python", workdir=str(tmp_path))
    assert "42" in out


def test_run_python_reports_nonzero_exit(tmp_path):
    out = run_code("import sys; sys.exit(3)", "python", workdir=str(tmp_path))
    assert "[exit 3]" in out


def test_captures_stderr(tmp_path):
    out = run_code("import sys; print('e', file=sys.stderr)", "python", workdir=str(tmp_path))
    assert "e" in out


def test_unsupported_language(tmp_path):
    assert "不支持的语言" in run_code("x", "cobol", workdir=str(tmp_path))


def test_blocked_pattern_rejected(tmp_path):
    assert "危险模式" in run_code("rm -rf /", "python", workdir=str(tmp_path))


def test_missing_workdir(tmp_path):
    missing = tmp_path / "nope"
    assert "工作目录不存在" in run_code("print(1)", "python", workdir=str(missing))


def test_stdin_passthrough(tmp_path):
    out = run_code("print(input())", "python", workdir=str(tmp_path), stdin="hello\n")
    assert "hello" in out


def test_code_runner_injects_executor():
    calls = {}

    def fake(code, language, *, workdir, timeout, stdin, max_output):
        calls["language"] = language
        calls["timeout"] = timeout
        return "OK"

    runner = CodeRunner(executor=fake, default_timeout=7)
    assert runner.run("x", "python") == "OK"
    assert calls == {"language": "python", "timeout": 7}


def test_python_interpreter_is_current(tmp_path):
    # 用当前解释器执行，避免环境缺少 python3 命令
    if sys.executable:
        out = run_code("print('ok')", "python", workdir=str(tmp_path))
        assert "ok" in out
