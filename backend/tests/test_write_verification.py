"""写后机械校验门禁（write-back verification gate）回归测试。

对应事故（2026-10-03）：`tool_write_file` 写出语法错误的 `tetris/game.js` 并回报成功，
模型读回后仍宣布任务完成。本模块把校验下沉到工具层，客观判定并把结果播报给模型。

核心不变量：
  1. 破损必须被抓到 —— 尤其是事故样本那种「结尾停在半句话、IIFE 未闭合」；
  2. 完好必须放行 —— 不能因为校验器本身有 bug 而误报，误报会训练模型忽略门禁；
  3. **不阻断** —— 分段写入的中间态天然语法不完整，抛错会堵死唯一可行的大文件工作流，
     所以只播报不改内容；
  4. 可校验项之外不留痕 —— .md 等文件不该产生噪音输出。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tools.fstools.verify import (  # noqa: E402
    readback_mismatch,
    verify_written_file,
)

# 事故文件残骸：结尾停在 `const dt = performance.now()` 半句话，IIFE 未闭合
BROKEN_JS = (
    "(function(){\n"
    "  const board = [];\n"
    "  function spawn(){\n"
    "    const dt = performance.now()"
)
GOOD_JS = (
    "(function(){\n"
    "  function tick(){\n"
    "    const dt = performance.now();\n"
    "    requestAnimationFrame(tick);\n"
    "  }\n"
    "  tick();\n"
    "})();\n"
)


@pytest.fixture()
def ws(tmp_path):
    return tmp_path


def _node_available() -> bool:
    return shutil.which("node") is not None


# ── 事故样本：破损 JS 必须被抓到，且报错要指向真正的错误 ──────────────────

@pytest.mark.skipif(not _node_available(), reason="node 不在 PATH")
def test_incident_broken_js_is_caught(ws):
    p = ws / "game.js"
    p.write_text(BROKEN_JS, encoding="utf-8")
    note = verify_written_file(p, existed_before=False)
    assert note.startswith("❌"), note
    assert "Unexpected end of input" in note, f"没抓到事故的真实错误：{note}"


@pytest.mark.skipif(not _node_available(), reason="node 不在 PATH")
def test_incident_broken_js_message_forbids_claiming_completion(ws):
    """单次写入即损坏 → 必须明确禁止宣告完成（这正是事故里缺的那句话）。"""
    p = ws / "game.js"
    p.write_text(BROKEN_JS, encoding="utf-8")
    note = verify_written_file(p, existed_before=False)
    assert "不要宣告任务完成" in note
    assert "tool_append_file" in note


def test_small_broken_js_is_not_silently_skipped(ws):
    """回归：曾用 200 字节门槛跳过 node --check，导致小破损文件静默通过。

    门槛在 60ms 的 node --check 面前不划算（一次 LLM 调用是它的百倍），
    而「小文件恰好破损」正是最容易被放过的一档。
    """
    p = ws / "tiny.js"
    p.write_text("function a(){", encoding="utf-8")
    assert len(p.read_bytes()) < 200, "样本必须小于旧门槛，否则本测试无效"
    assert verify_written_file(p, existed_before=False) != ""


# ── 完好文件必须放行（误报会训练模型忽略门禁）─────────────────────────

@pytest.mark.skipif(not _node_available(), reason="node 不在 PATH")
def test_good_js_passes(ws):
    p = ws / "game.js"
    p.write_text(GOOD_JS, encoding="utf-8")
    note = verify_written_file(p, existed_before=False)
    assert note.startswith("✅"), note
    assert "node --check" in note


def test_good_python_passes(ws):
    p = ws / "a.py"
    p.write_text("def f():\n    return 1\n", encoding="utf-8")
    assert verify_written_file(p, existed_before=False).startswith("✅")


def test_bad_python_reports_line_number(ws):
    p = ws / "a.py"
    p.write_text("def f():\n    return 1\n  bad\n", encoding="utf-8")
    note = verify_written_file(p, existed_before=False)
    assert note.startswith("❌")
    assert "第 3 行" in note


def test_good_and_bad_json(ws):
    ok = ws / "ok.json"
    ok.write_text(json.dumps({"a": [1, 2]}, ensure_ascii=False), encoding="utf-8")
    assert verify_written_file(ok, existed_before=False).startswith("✅")

    bad = ws / "bad.json"
    bad.write_text('{"a": 1, "b": [2, 3', encoding="utf-8")
    note = verify_written_file(bad, existed_before=False)
    assert note.startswith("❌")
    assert "JSON" in note


def test_css_brace_balance_ignores_comments_and_strings(ws):
    """CSS 粗检必须先剥注释/字符串，否则 /* { */ 和 content:"{" 会造成假失败。"""
    p = ws / "a.css"
    p.write_text(
        '/* 注释里有 { 和 } */\n.a::after { content: "}"; color: red; }\n',
        encoding="utf-8",
    )
    assert verify_written_file(p, existed_before=False).startswith("✅")

    broken = ws / "b.css"
    broken.write_text(".a { color: red;\n.b { color: blue; }\n", encoding="utf-8")
    assert verify_written_file(broken, existed_before=False).startswith("❌")


def test_html_tag_balance(ws):
    good = ws / "a.html"
    good.write_text("<html><body><script>var a=1;</script></body></html>", encoding="utf-8")
    assert verify_written_file(good, existed_before=False).startswith("✅")

    bad = ws / "b.html"
    bad.write_text("<html><body><script>var a=1;\n", encoding="utf-8")
    note = verify_written_file(bad, existed_before=False)
    assert note.startswith("❌")
    assert "<script>" in note


# ── 不阻断：分段写入的中间态 ──────────────────────────────────────────

@pytest.mark.skipif(not _node_available(), reason="node 不在 PATH")
def test_chunked_intermediate_state_is_warned_not_condemned(ws):
    """分段写入的中间态语法不完整是**正常的**，播报必须区分于「单次写坏」。"""
    p = ws / "game.js"
    p.write_text(BROKEN_JS, encoding="utf-8")
    note = verify_written_file(p, existed_before=True)
    assert note.startswith("⚠️"), note
    assert "中间态" in note
    assert "不要宣告任务完成" not in note, "中间态不该被当成单次写坏"


def test_verify_never_raises_and_never_modifies(tmp_path):
    """门禁只观测：不抛异常、不改内容。"""
    p = tmp_path / "bad.js"
    p.write_text(BROKEN_JS, encoding="utf-8")
    before = p.read_bytes()
    verify_written_file(p, existed_before=False)
    assert p.read_bytes() == before


def test_missing_file_and_unknown_suffix_are_silent(ws):
    assert verify_written_file(ws / "nope.js", existed_before=False) == ""
    md = ws / "a.md"
    md.write_text("# 标题\n随便写点东西\n", encoding="utf-8")
    assert verify_written_file(md, existed_before=False) == ""
    assert verify_written_file(ws, existed_before=False) == ""


# ── 读回校验 ────────────────────────────────────────────────────────

def test_readback_ok(ws):
    p = ws / "a.txt"
    p.write_text("hello", encoding="utf-8")
    assert readback_mismatch(p, "hello") is None


def test_readback_tolerates_crlf_difference(ws):
    """写入端按原样落 CRLF，读回若被规范化成 LF 不该误报。"""
    p = ws / "a.txt"
    p.write_bytes(b"a\r\nb")
    assert readback_mismatch(p, "a\nb") is None


def test_readback_detects_truncation(ws):
    """落盘内容比写入内容短 —— 正是「函数回报成功、文件却是半截」的那类静默失败。"""
    p = ws / "a.js"
    p.write_text("var a=1;", encoding="utf-8")
    warn = readback_mismatch(p, "var a=1; var b=2;")
    assert warn and "读回校验失败" in warn


# ── 工具层接线 ──────────────────────────────────────────────────────

def test_write_file_output_carries_verification(ws, monkeypatch):
    """门禁必须真的进到工具返回值里 —— 那是模型唯一能看到的地方。"""
    from app.tools.fstools import writer

    monkeypatch.setattr(writer, "_resolve", lambda p: ws / Path(p).name)
    monkeypatch.setattr(writer, "_ensure_safe", lambda *a, **k: None)
    monkeypatch.setattr(writer, "archive_external", lambda *a, **k: None)
    monkeypatch.setattr(writer, "record_write", lambda *a, **k: None)
    monkeypatch.setattr(writer._scan_cache, "invalidate", lambda *a, **k: None)

    env = writer.tool_write_file("bad.js", BROKEN_JS, overwrite=True)
    from app.tools.file_tools import unwrap

    out = unwrap(env)
    if _node_available():
        assert "Unexpected end of input" in out, out
    assert "bad.js" in out


def test_append_file_skips_readback_but_keeps_syntax_check(ws, monkeypatch):
    """append 的磁盘内容是「旧全文 + 追加」，拿片段做读回比对必然恒假告警。"""
    from app.tools.fstools import writer

    monkeypatch.setattr(writer, "_resolve", lambda p: ws / Path(p).name)
    monkeypatch.setattr(writer, "_ensure_safe", lambda *a, **k: None)
    monkeypatch.setattr(writer, "archive_external", lambda *a, **k: None)
    monkeypatch.setattr(writer, "record_write", lambda *a, **k: None)
    monkeypatch.setattr(writer._scan_cache, "invalidate", lambda *a, **k: None)

    (ws / "g.js").write_text(GOOD_JS, encoding="utf-8")
    env = writer.tool_append_file("g.js", "\n// 追加一行注释\n")

    from app.tools.file_tools import unwrap

    out = unwrap(env)
    assert "读回校验失败" not in out, f"append 不该做读回比对：{out}"
    assert "语法校验通过" in out, f"append 仍须做语法校验：{out}"