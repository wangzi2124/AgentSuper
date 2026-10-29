"""单元测试：LiteLLM debug 文件日志（轮转 + 脱敏）。"""

from __future__ import annotations

import logging

from config import settings
from observability import litellm_debug


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord("LiteLLM", logging.DEBUG, __file__, 1, message, (), None)


def test_redact_filter_masks_secrets():
    record = _record("Authorization: Bearer sk-abcdefghijklmnop and api_key=supersecret")
    litellm_debug.RedactFilter().filter(record)
    text = record.getMessage()
    assert "sk-abcdefghijklmnop" not in text
    assert "supersecret" not in text
    assert "[REDACTED]" in text


def test_configure_writes_redacted_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "litellm_debug", True)
    monkeypatch.setattr(settings, "litellm_debug_log", str(tmp_path / "litellm.log"))
    litellm_debug.reset_for_tests()
    litellm_debug.configure_litellm_debug()

    logger = logging.getLogger("LiteLLM")
    try:
        logger.debug("Authorization: Bearer sk-abcdefghijklmnop")
        for handler in logger.handlers:
            handler.flush()
        text = (tmp_path / "litellm.log").read_text(encoding="utf-8")
        assert "sk-abcdefghijklmnop" not in text
        assert "[REDACTED]" in text
        assert logger.handlers and logger.level == logging.DEBUG
    finally:
        for handler in list(logger.handlers):
            handler.close()
        logger.handlers = []
        litellm_debug.reset_for_tests()


def test_configure_respects_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "litellm_debug", False)
    monkeypatch.setattr(settings, "litellm_debug_log", str(tmp_path / "off.log"))
    litellm_debug.reset_for_tests()
    litellm_debug.configure_litellm_debug()
    assert not (tmp_path / "off.log").exists()
