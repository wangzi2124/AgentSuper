"""Minimal secret redaction for error surfaces (design §11.8)."""

from __future__ import annotations

import re

_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{8,}"),
    re.compile(r"(?i)\b(api[_-]?key|password|secret|token)\s*[=:]\s*([^\s,;]+)"),
)

_PROVIDER_RISK_TERMS = re.compile(r"独裁者|独裁政权|暴君")


def redact_secrets(text: str) -> str:
    value = str(text or "")
    value = _PATTERNS[0].sub("[REDACTED]", value)
    value = _PATTERNS[1].sub("Bearer [REDACTED]", value)
    value = _PATTERNS[2].sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
    return value


def sanitize_external_text(text: str) -> str:
    return _PROVIDER_RISK_TERMS.sub("[已过滤]", str(text or ""))


__all__ = ["redact_secrets", "sanitize_external_text"]
