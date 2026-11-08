"""Formatter runner and matching service (opencode gap G-03).

Commands come only from server config; they never receive model input directly.
The file path is passed as a separate argv element with ``shell=False``.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from config import settings

from .builtin import BUILTIN_FORMATTERS
from .spec import FormatterResult, FormatterSpec

logger = logging.getLogger(__name__)


def _safe_env(spec: FormatterSpec) -> dict[str, str]:
    keys = ("PATH", "HOME", "USERPROFILE", "SystemRoot", "TEMP", "TMP", "LANG", "LC_ALL")
    env = {key: os.environ[key] for key in keys if key in os.environ}
    env.update(spec.environment)
    return env


def _available(spec: FormatterSpec) -> bool:
    executable = spec.command[0] if spec.command else ""
    return bool(executable) and shutil.which(executable) is not None


def run_formatter(
    path: str | Path, workspace: str | Path, spec: FormatterSpec
) -> FormatterResult:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [*spec.command, str(path)],
            shell=False,
            cwd=str(workspace),
            env=_safe_env(spec),
            capture_output=True,
            text=True,
            timeout=spec.timeout,
        )
    except subprocess.TimeoutExpired:
        return FormatterResult(
            status="failed",
            formatter=spec.name,
            duration=time.perf_counter() - started,
            output="formatter timed out",
            path=str(path),
        )
    except OSError as exc:
        return FormatterResult(
            status="failed",
            formatter=spec.name,
            duration=time.perf_counter() - started,
            output=str(exc),
            path=str(path),
        )
    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    return FormatterResult(
        status="success" if completed.returncode == 0 else "failed",
        formatter=spec.name,
        duration=time.perf_counter() - started,
        output=output[:4000],
        path=str(path),
    )


def _config_specs(raw: Any) -> tuple[list[FormatterSpec], set[str]]:
    if raw is False or raw is None:
        return [], set()
    if raw is True:
        return list(BUILTIN_FORMATTERS), set()
    if not isinstance(raw, dict):
        return list(BUILTIN_FORMATTERS), set()
    if raw.get("enabled") is False:
        return [], set()
    items = raw.get("items") or raw.get("formatters") or raw.get("commands") or []
    specs: list[FormatterSpec] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                spec = FormatterSpec.from_dict(item)
                if spec is not None:
                    specs.append(spec)
    disabled = {str(name) for name in raw.get("disabled") or []}
    return specs, disabled


class FormatterService:
    def __init__(self, specs: list[FormatterSpec], disabled: set[str] | None = None) -> None:
        self.specs = list(specs)
        self.disabled = disabled or set()

    @classmethod
    def from_settings(cls) -> "FormatterService":
        specs, disabled = _config_specs(getattr(settings, "formatter", False))
        return cls(specs, disabled)

    @property
    def enabled(self) -> bool:
        return bool(self.specs)

    def _matches(self, spec: FormatterSpec, path: Path) -> bool:
        suffix = path.name[path.name.rfind(".") :] if "." in path.name else ""
        return suffix in spec.extensions

    def apply(self, path: str | Path, workspace: str | Path) -> FormatterResult:
        target = Path(path)
        if not target.is_file():
            return FormatterResult(status="skipped", path=str(target))
        if not self.enabled:
            return FormatterResult(status="skipped", path=str(target))
        last: FormatterResult | None = None
        for spec in self.specs:
            if spec.disabled or spec.name in self.disabled:
                continue
            if not self._matches(spec, target):
                continue
            if not _available(spec):
                continue
            result = run_formatter(target, workspace, spec)
            if result.status == "success":
                return result
            last = result
            logger.warning("formatter %s failed for %s: %s", spec.name, target, result.output)
        if last is not None:
            return last
        return FormatterResult(status="skipped", path=str(target))


def apply_formatters(path: str | Path, workspace: str | Path) -> FormatterResult | None:
    """Best-effort formatting hook used by file mutations."""
    try:
        return FormatterService.from_settings().apply(path, workspace)
    except Exception as exc:  # noqa: BLE001
        logger.warning("formatter service failed: %s", exc)
        return None


__all__ = ["FormatterService", "apply_formatters", "run_formatter"]
