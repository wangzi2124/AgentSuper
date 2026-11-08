"""Read snapshots and mutation summaries (opencode gap G-03).

The agent sees line-numbered snapshots so it can cite locations, and receives a
syntax verdict after writes. `VirtualFileSystem` itself keeps returning raw text:
formatting is an agent-facing concern.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from execution.filesystem import current_active_root

from .base import CheckResult, Formatter
from .checks import (
    JsonFormatter,
    MarkdownFormatter,
    PythonFormatter,
    TextFormatter,
    TomlFormatter,
    YamlFormatter,
)

MAX_READ_LINES = 400

_BY_EXT: dict[str, Formatter] = {
    ".py": PythonFormatter(),
    ".pyi": PythonFormatter(),
    ".json": JsonFormatter(),
    ".jsonc": JsonFormatter(),
    ".yaml": YamlFormatter(),
    ".yml": YamlFormatter(),
    ".toml": TomlFormatter(),
    ".md": MarkdownFormatter(),
    ".markdown": MarkdownFormatter(),
    ".txt": TextFormatter(),
}

_READ_TOOLS = {"read_file"}
_WRITE_TOOLS = {"write_file", "edit_file", "append_file"}


def formatter_for(path: str) -> Formatter:
    return _BY_EXT.get(Path(str(path or "")).suffix.lower(), TextFormatter())


def format_snapshot(path: str, content: str, *, max_lines: int = MAX_READ_LINES) -> str:
    """Return a ``cat -n`` style snapshot with an explicit truncation notice."""
    if not content:
        return content
    lines = content.splitlines()
    total = len(lines)
    shown = lines[:max_lines]
    width = len(str(total))
    numbered = "\n".join(f"{index:>{width}}\t{line}" for index, line in enumerate(shown, 1))
    if total > len(shown):
        numbered += f"\n...（共 {total} 行，已截断，仅显示前 {len(shown)} 行）"
    return numbered


def read_snapshot(path: str, content: str) -> str:
    return format_snapshot(path, content)


def check_text(path: str, text: str) -> CheckResult:
    return formatter_for(path).check(text)


def _read_after_write(path: str, workspace: str | Path | None) -> str:
    root = Path(workspace) if workspace else current_active_root()
    if root is None:
        return ""
    target = (Path(root) / str(path or "")).resolve()
    try:
        target.relative_to(Path(root).resolve())
    except ValueError:
        return ""
    try:
        return target.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return ""


def format_tool_output(
    tool_name: str,
    args: dict[str, Any],
    output: str,
    *,
    workspace: str | Path | None = None,
) -> str:
    """Agent-facing normalization applied inside the tool loop."""
    if not output or output.startswith("错误"):
        return output
    path = str(args.get("path") or "")
    if tool_name in _READ_TOOLS and path:
        return read_snapshot(path, output)
    if tool_name in _WRITE_TOOLS and path:
        text = output
        if tool_name == "write_file":
            text = str(args.get("content") or "")
        else:
            text = _read_after_write(path, workspace)
        if not text:
            return output
        result = check_text(path, text)
        if result.ok:
            if result.note and result.note not in ("该类型不做语法检查",):
                return f"{output}\n\n[{result.note}]"
            return output
        return f"{output}\n\n[{result.summary()}]"
    return output


__all__ = [
    "MAX_READ_LINES",
    "check_text",
    "format_snapshot",
    "format_tool_output",
    "formatter_for",
    "read_snapshot",
]
