"""Per-language syntax checks (opencode gap G-03).

Checks are best-effort and must never raise or execute user code.
"""

from __future__ import annotations

import ast
import json
import tomllib

import yaml

from .base import CheckResult, Formatter


class PythonFormatter(Formatter):
    language = "python"

    def check(self, text: str) -> CheckResult:
        try:
            ast.parse(text)
        except SyntaxError as exc:
            return CheckResult.failed(
                exc.msg or "invalid syntax",
                line=int(exc.lineno or 0),
                column=int(exc.offset or 0),
            )
        except (ValueError, TypeError) as exc:
            return CheckResult.failed(str(exc))
        return CheckResult.clean("Python 语法检查通过")


class JsonFormatter(Formatter):
    language = "json"

    def check(self, text: str) -> CheckResult:
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return CheckResult.failed(exc.msg, line=exc.lineno, column=exc.colno)
        return CheckResult.clean("JSON 校验通过")


class YamlFormatter(Formatter):
    language = "yaml"

    def check(self, text: str) -> CheckResult:
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            return CheckResult.failed(
                getattr(exc, "problem", None) or str(exc),
                line=int(getattr(mark, "line", 0) or 0) + 1,
                column=int(getattr(mark, "column", 0) or 0) + 1,
            )
        return CheckResult.clean("YAML 校验通过")


class TomlFormatter(Formatter):
    language = "toml"

    def check(self, text: str) -> CheckResult:
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            return CheckResult.failed(str(exc))
        return CheckResult.clean("TOML 校验通过")


class MarkdownFormatter(Formatter):
    language = "markdown"


class TextFormatter(Formatter):
    language = "text"


__all__ = [
    "JsonFormatter",
    "MarkdownFormatter",
    "PythonFormatter",
    "TextFormatter",
    "TomlFormatter",
    "YamlFormatter",
]
