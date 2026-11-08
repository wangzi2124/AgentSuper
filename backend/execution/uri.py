"""Attachment URI parsing with optional line ranges (opencode gap G-16)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

_RANGE = re.compile(r"^L?(\d+)(?:-L?(\d+))?$", re.IGNORECASE)


@dataclass(frozen=True)
class FileUri:
    path: str
    start: int = 0
    end: int = 0


def parse_file_uri(value: str) -> FileUri | None:
    """Parse ``file:///abs/path#L10-L20`` (``#10-20`` also accepted)."""
    text = str(value or "").strip()
    if not text.lower().startswith("file://"):
        return None
    parsed = urlparse(text)
    path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    if parsed.netloc and parsed.netloc not in ("", "localhost"):
        path = f"//{parsed.netloc}{path}"
    start = end = 0
    fragment = (parsed.fragment or "").strip()
    if fragment:
        match = _RANGE.match(fragment)
        if not match:
            raise ValueError(f"invalid line range: {fragment}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start:
            raise ValueError(f"invalid line range: {fragment}")
    return FileUri(path=str(Path(path)), start=start, end=end)


def slice_lines(text: str, start: int, end: int) -> str:
    if start <= 0:
        return text
    lines = text.splitlines()
    return "\n".join(lines[start - 1 : end])


__all__ = ["FileUri", "parse_file_uri", "slice_lines"]
