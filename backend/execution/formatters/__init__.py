"""Language-aware formatters (opencode gap G-03)."""

from .base import CheckResult, Formatter, Issue
from .registry import (
    MAX_READ_LINES,
    check_text,
    format_snapshot,
    format_tool_output,
    formatter_for,
    read_snapshot,
)
from .service import FormatterService, apply_formatters, run_formatter
from .spec import FormatterResult, FormatterSpec

__all__ = [
    "MAX_READ_LINES",
    "CheckResult",
    "Formatter",
    "FormatterResult",
    "FormatterService",
    "FormatterSpec",
    "Issue",
    "apply_formatters",
    "check_text",
    "format_snapshot",
    "format_tool_output",
    "formatter_for",
    "read_snapshot",
    "run_formatter",
]
