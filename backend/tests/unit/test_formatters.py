"""G-03: read snapshots and language-aware mutation checks."""

from __future__ import annotations

from pathlib import Path

from execution.formatters import check_text, format_snapshot, format_tool_output


def test_snapshot_numbers_lines():
    out = format_snapshot("a.py", "alpha\nbeta")
    assert out == "1\talpha\n2\tbeta"


def test_snapshot_truncates_with_notice():
    out = format_snapshot("a.txt", "\n".join("x" for _ in range(10)), max_lines=3)
    assert "1\tx" in out
    assert "共 10 行" in out
    assert out.count("\n") == 3


def test_python_check_ok_and_error():
    assert check_text("a.py", "def f():\n    return 1\n").ok
    result = check_text("a.py", "def f(:\n")
    assert result.ok is False
    assert "语法检查失败" in result.summary()
    assert result.issues[0].line > 0


def test_json_check_reports_location():
    result = check_text("a.json", '{"a": }')
    assert result.ok is False
    assert result.issues[0].column > 0


def test_yaml_and_toml_checks():
    assert check_text("a.yaml", "a: 1\n").ok
    assert check_text("a.yaml", "a: [\n").ok is False
    assert check_text("a.toml", "a = 1\n").ok
    assert check_text("a.toml", "a = \n").ok is False


def test_format_read_tool_output():
    out = format_tool_output("read_file", {"path": "a.py"}, "x = 1\nprint(x)")
    assert out.startswith("1\tx = 1")
    assert "2\tprint(x)" in out


def test_format_write_tool_output_adds_verdict():
    ok = format_tool_output("write_file", {"path": "a.py", "content": "x = 1\n"}, "已写入 a.py")
    assert "Python 语法检查通过" in ok
    bad = format_tool_output("write_file", {"path": "a.py", "content": "x = (\n"}, "已写入 a.py")
    assert "语法检查失败" in bad


def test_edit_checks_file_on_disk(tmp_path):
    target = Path(tmp_path) / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    out = format_tool_output(
        "edit_file", {"path": "a.py"}, "已编辑 a.py", workspace=tmp_path
    )
    assert "语法检查失败" in out or "Python 语法检查通过" in out


def test_errors_and_unknown_tools_pass_through():
    assert format_tool_output("read_file", {"path": "a.py"}, "错误：不存在") == "错误：不存在"
    assert format_tool_output("grep", {"path": "a.py"}, "hit") == "hit"
