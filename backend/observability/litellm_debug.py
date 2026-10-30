"""LiteLLM debug logging (default on, file-backed, redacted)."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import settings
from harness.security import redact_secrets

logger = logging.getLogger(__name__)

_MAX_BYTES = 20 * 1024 * 1024
_BACKUP_COUNT = 3
_DEFAULT_NAME = "litellm-debug.log"

_configured = False


class RedactFilter(logging.Filter):
    """Rewrite log records so API keys / bearer tokens never reach disk."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            record.msg = redact_secrets(record.getMessage())
            record.args = ()
        except Exception:  # noqa: BLE001 - never drop a record due to redaction
            pass
        return True


def configure_litellm_debug() -> None:
    """Enable LiteLLM debug and route it to a rotating, redacted file."""
    global _configured
    if _configured:
        return
    from config import settings

    if not bool(getattr(settings, "litellm_debug", True)):
        return
    try:
        import litellm

        litellm._turn_on_debug()
    except Exception:  # noqa: BLE001 - private API across litellm versions
        logger.warning("litellm._turn_on_debug() unavailable", exc_info=True)
        return

    litellm_logger = logging.getLogger("LiteLLM")
    litellm_logger.setLevel(logging.DEBUG)
    litellm_logger.propagate = False

    from storage.paths import data_path, global_paths

    override = (settings.litellm_debug_log or "").strip()
    path = (
        data_path(override, _DEFAULT_NAME)
        if override
        else global_paths()["log"] / _DEFAULT_NAME
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 - read-only FS should not block startup
        logger.warning("cannot open LiteLLM debug log at %s", path, exc_info=True)
        return
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    handler.addFilter(RedactFilter())
    litellm_logger.handlers = [handler]
    _configured = True
    logger.info("LiteLLM debug logging -> %s", path)


def reset_for_tests() -> None:
    global _configured
    _configured = False


__all__ = ["RedactFilter", "configure_litellm_debug", "reset_for_tests"]
