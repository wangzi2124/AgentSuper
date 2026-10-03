"""写后机械校验（write-back verification gate）。

**为什么需要**：2026-10-03 实测事故 —— 模型被要求「在 D 盘写个俄罗斯方块游戏」，
`tool_write_file` 写出的 ``tetris/game.js`` 24,877 字节，结尾停在
``const dt = performance.now()`` 半句话、花括号 +2 / 圆括号 +2 不配平、IIFE ``})();``
缺失；``node --check`` 报 ``SyntaxError: Unexpected end of input``。而 ``tool_write_file``
回报的是 ``Created tetris/game.js (24877 bytes)`` —— **一次成功的写入，产出的是语法错误
的文件**。模型接着读了一遍自己的文件、然后宣布任务完成。

纯靠提示词要求模型「写完自查」不可靠：模型读回损坏文件时不会主动承认。所以把校验做成
**工具层的机械门禁** —— 每次写/追加/编辑后由代码客观判定，结果直接拼进工具返回值，
下一轮模型必然看到（工具结果会作为 ``role="tool"`` 消息回到上下文）。

**为什么是「提示」而不是「拒绝」**：本项目鼓励分段写大文件（见
``_TRUNCATED_ARGS_ERROR``：写入被输出上限截断时正是让模型改用 ``tool_append_file``
分段续写）。分段写入的**中间态本来就是语法不完整**的，若在此抛错拒绝，等于把唯一可行的
大文件工作流堵死。因此这里只做客观判定并如实播报，不改内容、不回滚、不抛异常。

判定失败时的播报文案显式区分两种情况：
  - 「文件此前已存在 / 疑似分段写入中」→ 提醒模型这是中间态，写完需再确认；
  - 单次完整写入即语法错误 → 明确要求修好再宣告完成。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

_NODE_CHECK_TIMEOUT = 10  # 秒

# node 可执行文件的探测结果缓存（None=未探测）。写文件是高频操作，别每次都 PATH 扫描。
_NODE_BIN: str | None | bool = None


def _node_bin() -> str | None:
    """返回 node 路径；探测不到返回 None（调用方据此静默跳过，不报错）。"""
    global _NODE_BIN
    if _NODE_BIN is None:
        _NODE_BIN = shutil.which("node") or False
    return _NODE_BIN or None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _first_lines(text: str, n: int = 3, limit: int = 300) -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()][:n]
    out = " / ".join(lines)
    return out[:limit]


def _check_js(path: Path) -> tuple[bool, str] | None:
    """node --check：真语法判定。node 不在 PATH 则返回 None（静默跳过）。"""
    node = _node_bin()
    if not node:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 - node 路径来自 PATH，参数为固定字面量
            [node, "--check", str(path)],
            capture_output=True, timeout=_NODE_CHECK_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None  # 环境异常，不冤枉模型
    except OSError:
        return None
    if proc.returncode == 0:
        return True, "node --check"
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    # node 报错首行形如 `C:\...\dir\file.js:4`，目录前缀冗余（末尾已附文件名），
    # 且 Windows 临时路径可能很长，挤掉真正的错误信息。
    err = re.sub(r"(?m)^\s*\S*[\\/][^\\/\s]*\.js:\d+", "<file>", err)
    return False, f"node --check 失败：{_first_lines(err)}"


def _check_py(path: Path, text: str) -> tuple[bool, str] | None:
    """compile() 只解析不执行，安全且无需子进程。"""
    try:
        compile(text, str(path), "exec")
    except SyntaxError as e:
        return False, f"Python 语法错误：第 {e.lineno} 行 {e.msg}"
    except ValueError as e:  # 例如源码含 NUL 字节
        return False, f"Python 源码无效：{e}"
    return True, "compile()"


def _check_json(path: Path, text: str) -> tuple[bool, str] | None:
    try:
        json.loads(text)
    except json.JSONDecodeError as e:
        return False, f"JSON 解析失败：第 {e.lineno} 行第 {e.colno} 列 {e.msg}"
    return True, "json.loads"


def _balanced(text: str, pairs: dict[str, str]) -> bool:
    stack: list[str] = []
    for ch in text:
        if ch in pairs:
            stack.append(ch)
        elif ch in pairs.values():
            if not stack or pairs[stack.pop()] != ch:
                return False
    return not stack


def _check_css(path: Path, text: str) -> tuple[bool, str] | None:
    # 粗检：剥离注释与字符串后查花括号配平。CSS 没有官方 parse-only CLI，
    # 够用来抓「被输出上限截断」这类最常见的破损。
    import re
    stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    stripped = re.sub(r'"[^"]*"|\'[^\']*\'', '""', stripped)
    if _balanced(stripped, {"{": "}"}):
        return True, "花括号配平"
    return False, "花括号不配平（疑似内容被截断）"


def _check_html(path: Path, text: str) -> tuple[bool, str] | None:
    import re
    bad: list[str] = []
    for tag in ("html", "body", "script", "head", "style"):
        opened = len(re.findall(rf"<{tag}\b", text, flags=re.I))
        closed = len(re.findall(rf"</{tag}\s*>", text, flags=re.I))
        if opened != closed:
            bad.append(f"<{tag}> {opened} 开 / {closed} 闭")
    if bad:
        return False, "标签不配平：" + "，".join(bad) + "（疑似内容被截断）"
    return True, "标签配平"


def verify_written_file(path: Path, *, existed_before: bool = False) -> str:
    """对刚写入的文件做语法校验，返回给模型看的播报文本。

    返回空串表示「无可校验项 / 环境不支持」，此时调用方不要在输出里留痕迹。
    """
    try:
        if not path.is_file():
            return ""
    except OSError:
        return ""

    suffix = path.suffix.lower()
    result: tuple[bool, str] | None = None

    if suffix in (".js", ".mjs", ".cjs"):
        # 不设大小门槛：node --check 约 60ms，相对一次 LLM 调用可忽略，
        # 而「小文件恰好破损」正是最容易被静默放过的一档。
        result = _check_js(path)
    else:
        text = _read_text(path)
        if text is not None:
            if suffix == ".py":
                result = _check_py(path, text)
            elif suffix == ".json":
                result = _check_json(path, text)
            elif suffix == ".css":
                result = _check_css(path, text)
            elif suffix in (".html", ".htm"):
                result = _check_html(path, text)

    if result is None:
        return ""
    ok, detail = result
    name = path.name
    if ok:
        return f"✅ 语法校验通过（{detail}）：{name}"

    # 失败：按「此前是否存在」区分中间态与真破损，避免误导模型去「修」一个正在写的文件
    if existed_before:
        return (
            f"⚠️ 语法校验失败（{detail}）：{name}\n"
            f"   若这是分段写入（tool_append_file）的中间态，可忽略；"
            f"但**在宣告任务完成前**必须再校验一次，确保文件能被解析。"
        )
    return (
        f"❌ 语法校验失败（{detail}）：{name}\n"
        f"   文件已写入，但内容**无法被解析**。这是单次写入就损坏，"
        f"不要宣告任务完成 —— 请用 tool_append_file 补齐缺失部分（或重写），"
        f"直到校验通过为止。"
    )


def readback_mismatch(path: Path, expected_text: str) -> str | None:
    """读回校验：磁盘内容与刚写入的文本不一致时返回告警，否则 None。

    捕获「函数返回成功但落盘内容不符」的静默失败（磁盘满、编码替换、并发覆盖等）。
    """
    try:
        actual = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"⚠️ 读回校验失败：无法读取刚写入的文件（{e}）"
    if actual == expected_text:
        return None
    if actual.replace("\r\n", "\n") == expected_text.replace("\r\n", "\n"):
        return None  # 仅行尾差异，写入语义正确
    return (
        f"⚠️ 读回校验失败：磁盘内容与写入内容不一致"
        f"（写入 {len(expected_text)} 字符，读回 {len(actual)} 字符）"
    )