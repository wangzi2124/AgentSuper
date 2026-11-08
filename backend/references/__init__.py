"""Named references: local paths and cached Git repositories (G-07)."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REFRESH_SECONDS = 24 * 3600
CLONE_TIMEOUT = 120
_TASKS: set[asyncio.Task] = set()
_LOCKS: dict[str, asyncio.Lock] = {}


@dataclass
class ReferenceSpec:
    name: str
    path: str = ""
    repository: str = ""
    branch: str = ""
    description: str = ""
    hidden: bool = False
    source: str = "global"

    @property
    def is_remote(self) -> bool:
        value = self.path or self.repository
        return not value.startswith((".", "/", "~")) and "://" in value or (
            bool(self.repository) and not self.path
        )


@dataclass
class ReferenceState:
    spec: ReferenceSpec
    status: str = "ready"
    error: str = ""
    root: str = ""
    updated_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.spec.name,
            "kind": "git" if self.spec.repository else "local",
            "description": self.spec.description,
            "hidden": self.spec.hidden,
            "source": self.spec.source,
            "status": self.status,
            "error": self.error,
            "root": self.root,
        }


def _dirs(workspace: str | Path | None) -> list[tuple[str, Path]]:
    dirs = [("global", Path.home() / ".config" / "agentsuper" / "references.json")]
    if workspace:
        dirs.append(("project", Path(workspace).expanduser() / ".agentsuper" / "references.json"))
    return dirs


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    refs = raw.get("references") if isinstance(raw, dict) else None
    return refs if isinstance(refs, dict) else {}


def load_specs(workspace: str | Path | None = None) -> dict[str, ReferenceSpec]:
    specs: dict[str, ReferenceSpec] = {}
    for source, path in _dirs(workspace):
        for name, item in _read(path).items():
            if not isinstance(item, dict):
                continue
            specs[str(name)] = ReferenceSpec(
                name=str(name),
                path=str(item.get("path") or ""),
                repository=str(item.get("repository") or ""),
                branch=str(item.get("branch") or ""),
                description=str(item.get("description") or ""),
                hidden=bool(item.get("hidden", False)),
                source=source,
            )
    return specs


def _cache_root(spec: ReferenceSpec) -> Path:
    from storage.paths import global_paths

    base = Path(global_paths().get("cache", Path.home() / ".cache")) / "references"
    repo = (spec.repository or spec.path).replace("://", "/").replace(":", "_")
    branch = spec.branch or "HEAD"
    return base / repo.replace("/", "_") / branch.replace("/", "_")


class ReferenceRegistry:
    def __init__(self, specs: dict[str, ReferenceSpec]) -> None:
        self.specs = specs
        self.states: dict[str, ReferenceState] = {}
        for spec in specs.values():
            self.states[spec.name] = self._initial_state(spec)

    def _initial_state(self, spec: ReferenceSpec) -> ReferenceState:
        if not spec.repository:
            root = Path(spec.path).expanduser()
            if not root.is_dir():
                return ReferenceState(spec, status="error", error=f"path not found: {root}")
            return ReferenceState(spec, status="ready", root=str(root), updated_at=time.time())
        root = _cache_root(spec)
        if root.is_dir():
            stale = time.time() - root.stat().st_mtime > REFRESH_SECONDS
            return ReferenceState(
                spec,
                status="stale" if stale else "ready",
                root=str(root),
                updated_at=root.stat().st_mtime,
            )
        return ReferenceState(spec, status="missing", root=str(root))

    def list(self, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        return [
            state.as_dict()
            for state in self.states.values()
            if include_hidden or not state.spec.hidden
        ]

    def get(self, name: str) -> ReferenceState | None:
        return self.states.get(name)

    def ensure(self, name: str) -> ReferenceState:
        state = self.states.get(name)
        if state is None:
            raise KeyError(name)
        if not state.spec.repository:
            return state
        if state.status in ("ready", "stale") and Path(state.root).is_dir():
            return state
        if state.status == "preparing":
            return state
        state.status = "preparing"
        task = asyncio.get_running_loop().create_task(self._prepare(state))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        return state

    async def _prepare(self, state: ReferenceState) -> None:
        key = state.root
        lock = _LOCKS.setdefault(key, asyncio.Lock())
        async with lock:
            try:
                await asyncio.to_thread(self._clone, state)
                state.status = "ready"
                state.error = ""
                state.updated_at = time.time()
            except Exception as exc:  # noqa: BLE001
                state.status = "error"
                state.error = str(exc) or repr(exc)
                logger.warning("reference clone failed for %s: %s", state.spec.name, exc)

    def _clone(self, state: ReferenceState) -> None:
        root = Path(state.root)
        root.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "clone", "--depth", "1"]
        if state.spec.branch:
            cmd += ["--branch", state.spec.branch]
        cmd += [state.spec.repository, str(root)]
        if root.exists():
            cmd = ["git", "-C", str(root), "fetch", "--depth", "1"]
        subprocess.run(
            cmd,
            check=True,
            timeout=CLONE_TIMEOUT,
            capture_output=True,
            text=True,
        )

    async def refresh(self, name: str) -> ReferenceState:
        state = self.states.get(name)
        if state is None:
            raise KeyError(name)
        if not state.spec.repository:
            return state
        if Path(state.root).exists():
            import shutil

            shutil.rmtree(state.root, ignore_errors=True)
        state.status = "missing"
        return self.ensure(name)

    def tree(self, name: str, *, limit: int = 200) -> list[str]:
        state = self.states.get(name)
        if state is None:
            raise KeyError(name)
        root = Path(state.root)
        if not root.is_dir():
            raise FileNotFoundError(root)
        out: list[str] = []
        for item in sorted(root.iterdir(), key=lambda p: p.name):
            out.append(item.name + ("/" if item.is_dir() else ""))
            if len(out) >= limit:
                break
        return out


def load_registry(workspace: str | Path | None = None) -> ReferenceRegistry:
    return ReferenceRegistry(load_specs(workspace))


def write_reference(
    name: str,
    payload: dict[str, Any],
    *,
    workspace: str | Path | None = None,
    scope: str = "global",
) -> None:
    path = (
        Path(workspace).expanduser() / ".agentsuper" / "references.json"
        if scope == "project"
        else Path.home() / ".config" / "agentsuper" / "references.json"
    )
    raw = {"references": _read(path)}
    if not isinstance(raw["references"], dict):
        raw["references"] = {}
    item = {k: v for k, v in payload.items() if k not in ("name", "scope")}
    raw["references"][name] = item
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")


def delete_reference(
    name: str, *, workspace: str | Path | None = None, scope: str = "global"
) -> bool:
    path = (
        Path(workspace).expanduser() / ".agentsuper" / "references.json"
        if scope == "project"
        else Path.home() / ".config" / "agentsuper" / "references.json"
    )
    refs = _read(path)
    if name not in refs:
        return False
    refs.pop(name, None)
    path.write_text(json.dumps({"references": refs}, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def reference_instructions(workspace: str | Path | None = None) -> str:
    registry = load_registry(workspace)
    lines = [
        f"- {state.spec.name}: {state.spec.description or state.spec.repository or state.spec.path}"
        for state in registry.states.values()
        if not state.spec.hidden and state.spec.description
    ]
    return "\n".join(lines)


def selection_text(workspace: str | Path | None, names: list[str]) -> str:
    """Root path + one-level listing for references explicitly selected in chat."""
    if not names:
        return ""
    registry = load_registry(workspace)
    lines: list[str] = []
    for name in names:
        state = registry.states.get(name)
        if state is None:
            lines.append(f"- {name}: (unknown reference)")
            continue
        if not Path(state.root).is_dir():
            lines.append(f"- {name}: ({state.status})")
            continue
        try:
            entries = registry.tree(name, limit=50)
        except Exception:  # noqa: BLE001
            entries = []
        suffix = ", ".join(entries) if entries else "(empty)"
        lines.append(f"- {name}: {state.root}\n  entries: {suffix}")
    return "[已选引用]\n" + "\n".join(lines)


__all__ = [
    "CLONE_TIMEOUT",
    "REFRESH_SECONDS",
    "ReferenceRegistry",
    "ReferenceSpec",
    "ReferenceState",
    "delete_reference",
    "load_registry",
    "load_specs",
    "reference_instructions",
    "selection_text",
    "write_reference",
]
