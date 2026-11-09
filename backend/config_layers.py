"""Layered JSONC configuration (opencode §4 parity).

Layers are merged low → high so later files win:

1. **system**  — ``%PROGRAMDATA%/agentsuper/config.json`` (Windows) or
   ``/etc/agentsuper/config.json`` (POSIX).
2. **global**  — ``~/.config/agentsuper/config.json``.
3. **project** — the nearest ``.agentsuper/config.json`` walking up from the
   working directory (or an explicit ``workspace``). Outer directories have
   lower priority than inner ones, matching opencode's "search upward" rule.
4. **app**     — ``backend/config.json`` written by ``persist_settings`` (the
   runtime overrides produced by the settings UI).

Environment variables still win over every file because ``Settings`` keeps
``env_settings`` above this source.

String values support two substitutions, applied recursively:

* ``{env:NAME}``  — read ``os.environ["NAME"]`` (missing → empty string)
* ``{file:path}`` — read a file's text; relative paths resolve against the
  directory of the config file that declared the value, ``~/`` is expanded.
"""

from __future__ import annotations

import copy
import os
import re
import sys
from pathlib import Path
from typing import Any

_ENV_REF = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
_FILE_REF = re.compile(r"\{file:([^}]+)\}")

GLOBAL_CONFIG_NAME = "config.json"
PROJECT_DIR_NAME = ".agentsuper"
PROJECT_CONFIG_NAMES = ("config.json", "config.jsonc")

# Project files come from cloned repositories, so they must not be able to
# relocate process storage, auth, or relax the sandbox. They may still tune
# model/tool/compaction behaviour.
PROJECT_DENIED_KEYS = frozenset(
    {
        "admin_token",
        "allow_source_writes",
        "cors_origins",
        "database_url",
        "host",
        "memory_persist_path",
        "permission_approval_timeout",
        "permissions",
        "port",
        "postgres_checkpoint_url",
        "session_db_path",
        "ssrf_allow_internal",
        "vector_store_path",
    }
)
PROJECT_DENIED_PREFIXES = ("agentsuper_", "auth_", "checkpoint_", "db_")


def _parse_jsonc(text: str) -> Any:
    # Imported lazily: ``config`` imports this module while building its
    # settings source, so a module-level import would be circular.
    from config import parse_jsonc

    return parse_jsonc(text)


def _read_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = _parse_jsonc(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def system_config_path() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        return Path(base) / "agentsuper" / GLOBAL_CONFIG_NAME
    return Path("/etc/agentsuper") / GLOBAL_CONFIG_NAME


def global_config_path() -> Path:
    return Path.home() / ".config" / "agentsuper" / GLOBAL_CONFIG_NAME


def _find_project_configs(start: Path) -> list[Path]:
    """Return project configs from outermost to innermost (inner wins)."""
    found: list[Path] = []
    current = start.resolve()
    for directory in [current, *current.parents]:
        for name in PROJECT_CONFIG_NAMES:
            candidate = directory / PROJECT_DIR_NAME / name
            if candidate.is_file():
                found.append(candidate)
                break
    found.reverse()
    return found


def app_config_path() -> Path:
    return Path(__file__).parent / "config.json"


def config_layers(workspace: str | Path | None = None) -> list[tuple[str, Path]]:
    """Ordered ``(source, path)`` layers, lowest priority first."""
    start = Path(workspace) if workspace else Path.cwd()
    layers: list[tuple[str, Path]] = [
        ("system", system_config_path()),
        ("global", global_config_path()),
    ]
    for path in _find_project_configs(start):
        layers.append(("project", path))
    layers.append(("app", app_config_path()))
    return layers


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base`` (override wins)."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _substitute_string(value: str, base_dir: Path) -> str:
    def env_repl(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), "")

    def file_repl(match: re.Match[str]) -> str:
        raw = match.group(1).strip()
        if not raw:
            return ""
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = base_dir / target
        try:
            return target.read_text(encoding="utf-8-sig").rstrip("\r\n")
        except OSError:
            return ""

    text = _ENV_REF.sub(env_repl, value)
    return _FILE_REF.sub(file_repl, text)


def substitute(value: Any, base_dir: str | Path) -> Any:
    """Recursively expand ``{env:}`` / ``{file:}`` in string values."""
    root = Path(base_dir)
    if isinstance(value, str):
        return _substitute_string(value, root)
    if isinstance(value, dict):
        return {key: substitute(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, root) for item in value]
    return value


def _filter_project_keys(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in raw.items()
        if key not in PROJECT_DENIED_KEYS
        and not key.startswith(PROJECT_DENIED_PREFIXES)
    }


def load_layered_config(workspace: str | Path | None = None) -> dict[str, Any]:
    """Merge every config layer and expand variable references.

    Each layer's string values are expanded against *its own* directory so a
    project config's ``{file:./secret}`` resolves inside the project.
    """
    merged: dict[str, Any] = {}
    for source, path in config_layers(workspace):
        raw = _read_config(path)
        if not raw:
            continue
        if source == "project":
            raw = _filter_project_keys(raw)
        merged = deep_merge(merged, substitute(raw, path.parent))
    return merged


__all__ = [
    "GLOBAL_CONFIG_NAME",
    "PROJECT_CONFIG_NAMES",
    "PROJECT_DIR_NAME",
    "app_config_path",
    "config_layers",
    "deep_merge",
    "global_config_path",
    "load_layered_config",
    "substitute",
    "system_config_path",
]
