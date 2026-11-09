"""Project instruction discovery and injection (opencode gap G-01).

Instructions are *not* prompt constants. They are resolved per turn from a
workspace so that ``AGENTS.md`` / configured files / URLs can change without a
restart, and so that the model view never reads the filesystem directly.

Discovery order (stable, earlier wins on duplicate content):

1. ``global``   — user-level ``AGENTS.md`` (config dir + ``~/.config/agentsuper``)
2. ``project``  — every ``AGENTS.md`` from the workspace up to home (outermost first)
3. ``config``   — ``get_settings().instructions`` entries (file / glob / ``~/`` / URL)
4. ``nested``   — ``AGENTS.md`` inside directories the agent touched last turn

Deduplication is by resolved path and by content hash. Change detection happens
on every resolve: a file is re-read when ``mtime``/size changes and simply
disappears from the result when deleted. Token budgeting reuses
``cognition.context_meter`` — there is no second tokenizer here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

from config import get_settings

logger = logging.getLogger(__name__)

InstructionKind = Literal["global", "project", "nested", "config", "url"]

INSTRUCTION_FILENAME = "AGENTS.md"
WORKSPACE_INSTRUCTIONS_DIR = ".agentsuper"
WORKSPACE_INSTRUCTIONS_FILE = "instructions.json"
_URL_TIMEOUT = 5.0
_URL_TTL = 60.0
_MAX_NESTED_DIRS = 200


def _has_glob(value: str) -> bool:
    return any(ch in value for ch in "*?[")


def detect_project_root(start: str | Path | None = None) -> Path:
    """Nearest project root (git repo, then AGENTS.md), like opencode's cwd rules."""
    try:
        current = Path(start or Path.cwd()).expanduser().resolve()
    except OSError:
        current = Path(start or Path.cwd())
    for directory in [current, *current.parents]:
        if (directory / ".git").exists():
            return directory
    for directory in [current, *current.parents]:
        if (directory / INSTRUCTION_FILENAME).is_file():
            return directory
    return current


def workspace_instructions_path(workspace: str | Path) -> Path:
    """Location of the per-workspace instruction override file."""
    return (
        Path(workspace).expanduser()
        / WORKSPACE_INSTRUCTIONS_DIR
        / WORKSPACE_INSTRUCTIONS_FILE
    )


def _clean_entries(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        value = str(item or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def _load_payload(workspace: str | Path) -> dict:
    path = workspace_instructions_path(workspace)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        logger.debug("cannot read workspace instructions %s", path, exc_info=True)
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        return {"instructions": raw}
    return {}


def _write_payload(workspace: str | Path, payload: dict) -> Path:
    path = workspace_instructions_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)
    return path


def read_workspace_instructions(workspace: str | Path) -> list[str]:
    """Read the raw entries managed from the UI (files / globs / URLs)."""
    return _clean_entries(_load_payload(workspace).get("instructions"))


def read_workspace_auto_discover(workspace: str | Path) -> bool:
    """Whether ``AGENTS.md`` auto-discovery is enabled for this workspace."""
    value = _load_payload(workspace).get("auto_discover")
    return value if isinstance(value, bool) else True


def write_workspace_instructions(workspace: str | Path, entries: Iterable[str]) -> Path:
    """Atomically persist the workspace instruction entries."""
    payload = _load_payload(workspace)
    payload["instructions"] = _clean_entries(list(entries))
    return _write_payload(workspace, payload)


def write_workspace_auto_discover(workspace: str | Path, enabled: bool) -> Path:
    """Toggle automatic ``AGENTS.md`` discovery for this workspace."""
    payload = _load_payload(workspace)
    payload["auto_discover"] = bool(enabled)
    payload.setdefault("instructions", [])
    return _write_payload(workspace, payload)


def add_workspace_instruction(workspace: str | Path, entry: str) -> list[str]:
    entries = read_workspace_instructions(workspace)
    value = str(entry or "").strip()
    if value and value not in entries:
        entries.append(value)
    write_workspace_instructions(workspace, entries)
    return entries


def remove_workspace_instruction(workspace: str | Path, entry: str) -> list[str]:
    value = str(entry or "").strip()
    entries = [item for item in read_workspace_instructions(workspace) if item != value]
    write_workspace_instructions(workspace, entries)
    return entries


@dataclass(frozen=True)
class InstructionSource:
    """One instruction origin (metadata only; content lives in a block)."""

    id: str
    kind: InstructionKind
    location: str
    scope: str = ""
    mtime: float = 0.0
    content_hash: str = ""


@dataclass
class InstructionBlock:
    """A loaded instruction block ready to be appended after the stable head."""

    source: InstructionSource
    content: str
    tokens: int = 0

    @property
    def chars(self) -> int:
        return len(self.content)


@dataclass
class _Candidate:
    source: InstructionSource
    content: str = ""
    loaded: bool = False
    skipped_reason: str = ""
    tokens: int = 0


@dataclass
class _FileCacheEntry:
    mtime: float
    size: int
    content: str
    content_hash: str


def _norm(path: Path | str) -> str:
    try:
        return str(Path(path).resolve()).replace("\\", "/").lower()
    except OSError:
        return str(path).replace("\\", "/").lower()


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _is_url(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))


def _estimate_tokens(text: str, model_id: str) -> int:
    if not text:
        return 0
    try:
        from cognition.context_meter import get_meter

        return int(get_meter().estimate([], system=text, model_id=model_id).system)
    except Exception:  # noqa: BLE001 - accounting must never break a turn
        return 0


def _global_candidates() -> list[Path]:
    out: list[Path] = [Path.home() / ".config" / "agentsuper" / INSTRUCTION_FILENAME]
    try:
        from storage.paths import global_paths

        out.append(global_paths()["config"] / INSTRUCTION_FILENAME)
    except Exception:  # noqa: BLE001 - data root unavailable during early tests
        logger.debug("cannot resolve config root for global instructions", exc_info=True)
    seen: set[str] = set()
    unique: list[Path] = []
    for item in out:
        key = _norm(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _upward_dirs(workspace: Path) -> list[Path]:
    """Workspace → home (inclusive) or filesystem root, outermost first."""
    dirs: list[Path] = []
    current = workspace
    home = Path.home().resolve()
    while True:
        dirs.append(current)
        if current == current.parent:
            break
        if current == home:
            break
        current = current.parent
    return list(reversed(dirs))


class InstructionResolver:
    """Discover, cache, and budget project instructions for a workspace."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._file_cache: dict[str, _FileCacheEntry] = {}
        self._url_cache: dict[str, tuple[float, str, str]] = {}
        # workspace key -> directories the agent has touched
        self._accessed: dict[str, list[str]] = {}

    # ── access tracking ────────────────────────────────────────────────
    def note_access(self, path: str | Path, workspace: str | Path | None = None) -> None:
        """Record a directory the file tools touched (nested AGENTS.md hint)."""
        try:
            p = Path(path).resolve()
        except OSError:
            return
        directory = p if p.is_dir() else p.parent
        ws_value = str(workspace or "").strip()
        if ws_value:
            try:
                workspace_path = Path(ws_value).resolve()
            except OSError:
                return
        else:
            # Fall back to the longest known workspace that contains the path.
            workspace_path = self._best_workspace_for(directory)
            if workspace_path is None:
                return
        try:
            directory.relative_to(workspace_path)
        except ValueError:
            return
        key = _norm(workspace_path)
        resolved = str(directory)
        with self._lock:
            items = self._accessed.setdefault(key, [])
            if resolved not in items:
                items.append(resolved)
                if len(items) > _MAX_NESTED_DIRS:
                    del items[0 : len(items) - _MAX_NESTED_DIRS]

    def _best_workspace_for(self, directory: Path) -> Path | None:
        best: Path | None = None
        for key in list(self._accessed):
            try:
                candidate = Path(key)
                directory.relative_to(candidate)
            except (ValueError, OSError):
                continue
            if best is None or len(str(candidate)) > len(str(best)):
                best = candidate
        return best

    def clear(self) -> None:
        with self._lock:
            self._file_cache.clear()
            self._url_cache.clear()
            self._accessed.clear()

    # ── file / url reading ─────────────────────────────────────────────
    def _read_file(self, path: Path) -> tuple[bool, str, float, str, str]:
        """Return ``(ok, content, mtime, hash, reason)``."""
        key = str(path)
        try:
            stat = path.stat()
        except OSError:
            with self._lock:
                self._file_cache.pop(key, None)
            return False, "", 0.0, "", "missing"
        with self._lock:
            cached = self._file_cache.get(key)
        if cached is not None and cached.mtime == stat.st_mtime and cached.size == stat.st_size:
            return True, cached.content, cached.mtime, cached.content_hash, ""
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.debug("cannot read instruction %s: %s", path, exc)
            return False, "", stat.st_mtime, "", "unreadable"
        digest = _hash(content)
        with self._lock:
            self._file_cache[key] = _FileCacheEntry(
                mtime=stat.st_mtime, size=stat.st_size, content=content, content_hash=digest
            )
        return True, content, stat.st_mtime, digest, ""

    def _read_url(self, url: str) -> tuple[bool, str, str, str]:
        """Return ``(ok, content, hash, reason)``; 5s timeout, short TTL cache."""
        now = time.time()
        with self._lock:
            cached = self._url_cache.get(url)
        if cached is not None and now - cached[0] < _URL_TTL:
            return True, cached[1], cached[2], ""
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "agentsuper-instructions/1.0"}
            )
            with urllib.request.urlopen(request, timeout=_URL_TIMEOUT) as response:  # noqa: S310 - config-only URL
                raw = response.read()
            content = raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - a bad URL must not break a turn
            logger.debug("instruction url failed %s: %s", url, exc)
            return False, "", "", "url_failed"
        digest = _hash(content)
        with self._lock:
            self._url_cache[url] = (now, content, digest)
        return True, content, digest, ""

    # ── discovery ──────────────────────────────────────────────────────
    def _collect(self, workspace: str | Path, model_id: str = "") -> list[_Candidate]:
        try:
            ws = Path(workspace).resolve()
        except OSError:
            ws = Path(workspace)
        scope = str(ws)
        seen_paths: set[str] = set()
        seen_hashes: set[str] = set()
        out: list[_Candidate] = []

        def add_file(path: Path, kind: InstructionKind) -> None:
            key = _norm(path)
            if key in seen_paths:
                return
            seen_paths.add(key)
            ok, content, mtime, digest, reason = self._read_file(path)
            source = InstructionSource(
                id=f"{kind}:{key}",
                kind=kind,
                location=str(path),
                scope=scope,
                mtime=mtime,
                content_hash=digest,
            )
            if not ok:
                out.append(_Candidate(source=source, skipped_reason=reason))
                return
            if digest in seen_hashes:
                return
            seen_hashes.add(digest)
            out.append(_Candidate(source=source, content=content, loaded=True))

        def add_url(url: str) -> None:
            key = f"url:{url}"
            if key in seen_paths:
                return
            seen_paths.add(key)
            ok, content, digest, reason = self._read_url(url)
            source = InstructionSource(
                id=key, kind="url", location=url, scope=scope, content_hash=digest
            )
            if not ok:
                out.append(_Candidate(source=source, skipped_reason=reason))
                return
            if digest in seen_hashes:
                return
            seen_hashes.add(digest)
            out.append(_Candidate(source=source, content=content, loaded=True))

        auto_discover = read_workspace_auto_discover(ws)
        if auto_discover:
            for path in _global_candidates():
                if path.is_file():
                    add_file(path, "global")

            for directory in _upward_dirs(ws):
                candidate = directory / INSTRUCTION_FILENAME
                if candidate.is_file():
                    add_file(candidate, "project")

        def add_entry(entry: str, kind: InstructionKind = "config") -> None:
            value = str(entry or "").strip()
            if not value:
                return
            if _is_url(value):
                add_url(value)
                return
            expanded = Path(value).expanduser()
            if not expanded.is_absolute():
                base = ws / expanded
            else:
                base = expanded
            if _has_glob(str(base)):
                try:
                    matches = (
                        sorted(ws.glob(value))
                        if not expanded.is_absolute()
                        else sorted(
                            Path(expanded.anchor).glob(
                                str(expanded)[len(expanded.anchor):]
                            )
                        )
                    )
                except (ValueError, OSError):
                    matches = []
                for match in matches:
                    if match.is_file():
                        add_file(match, kind)
            else:
                if base.is_file():
                    add_file(base, kind)

        for raw in list(getattr(get_settings(), "instructions", []) or []):
            add_entry(raw, "config")

        for entry in read_workspace_instructions(ws):
            add_entry(entry, "config")

        if auto_discover:
            with self._lock:
                nested_dirs = list(self._accessed.get(_norm(ws), []))
            for directory in nested_dirs:
                candidate = Path(directory) / INSTRUCTION_FILENAME
                if candidate.is_file():
                    add_file(candidate, "nested")

        for candidate in out:
            if candidate.loaded:
                candidate.tokens = _estimate_tokens(candidate.content, model_id)
        return out

    # ── budgeting ──────────────────────────────────────────────────────
    def _fit(
        self, candidates: Iterable[_Candidate], model_id: str
    ) -> list[InstructionBlock]:
        max_chars = int(getattr(get_settings(), "instructions_max_chars", 0) or 0)
        max_tokens = int(getattr(get_settings(), "instructions_max_tokens", 0) or 0)
        if max_chars <= 0 and max_tokens <= 0:
            return [
                InstructionBlock(source=c.source, content=c.content, tokens=c.tokens)
                for c in candidates
                if c.loaded and c.content.strip()
            ]

        blocks: list[InstructionBlock] = []
        used_tokens = 0
        used_chars = 0
        for candidate in candidates:
            if not candidate.loaded:
                continue
            text = candidate.content.strip()
            if not text:
                continue
            tokens = candidate.tokens
            remaining_tokens = (max_tokens - used_tokens) if max_tokens > 0 else tokens
            remaining_chars = (max_chars - used_chars) if max_chars > 0 else len(text)
            if remaining_tokens <= 0 or remaining_chars <= 0:
                break
            if tokens <= remaining_tokens and len(text) <= remaining_chars:
                blocks.append(
                    InstructionBlock(source=candidate.source, content=text, tokens=tokens)
                )
                used_tokens += tokens
                used_chars += len(text)
                continue
            # Truncate this block to whatever budget is left, then stop: later
            # sources are lower priority and must not jump ahead of this one.
            marker = "\n[指令已截断]"
            keep_chars = remaining_chars
            if len(marker) < remaining_chars:
                keep_chars = remaining_chars - len(marker)
            if max_tokens > 0:
                # ~4 chars/token estimate, refined by the canonical meter below.
                keep_chars = min(keep_chars, max(1, remaining_tokens * 4))
            kept = text[:keep_chars].rstrip()
            if len(kept) + len(marker) <= remaining_chars:
                kept += marker
            kept_tokens = _estimate_tokens(kept, model_id)
            if kept.strip():
                blocks.append(
                    InstructionBlock(
                        source=candidate.source, content=kept, tokens=kept_tokens
                    )
                )
                used_tokens += kept_tokens
                used_chars += len(kept)
            break
        return blocks

    def resolve(self, workspace: str | Path, model_id: str = "") -> list[InstructionBlock]:
        """Resolve the blocks to inject for one turn (empty when disabled)."""
        if not bool(getattr(get_settings(), "instructions_enabled", True)):
            return []
        return self._fit(self._collect(workspace, model_id), model_id)

    def describe(self, workspace: str | Path, model_id: str = "") -> list[dict[str, object]]:
        """Metadata-only view for the API (never returns block bodies)."""
        if not bool(getattr(get_settings(), "instructions_enabled", True)):
            return []
        out: list[dict[str, object]] = []
        for candidate in self._collect(workspace, model_id):
            out.append(
                {
                    "id": candidate.source.id,
                    "kind": candidate.source.kind,
                    "location": candidate.source.location,
                    "scope": candidate.source.scope,
                    "mtime": candidate.source.mtime,
                    "hash": candidate.source.content_hash,
                    "loaded": bool(candidate.loaded),
                    "skipped_reason": candidate.skipped_reason,
                    "chars": len(candidate.content) if candidate.loaded else 0,
                    "tokens": int(candidate.tokens) if candidate.loaded else 0,
                }
            )
        return out


_RESOLVER: InstructionResolver | None = None
_RESOLVER_LOCK = threading.Lock()


def get_instruction_resolver() -> InstructionResolver:
    global _RESOLVER
    if _RESOLVER is None:
        with _RESOLVER_LOCK:
            if _RESOLVER is None:
                _RESOLVER = InstructionResolver()
    return _RESOLVER


def reset_instruction_resolver() -> None:
    """Test helper: drop the process-wide resolver."""
    global _RESOLVER
    with _RESOLVER_LOCK:
        _RESOLVER = None


__all__ = [
    "INSTRUCTION_FILENAME",
    "WORKSPACE_INSTRUCTIONS_DIR",
    "WORKSPACE_INSTRUCTIONS_FILE",
    "InstructionBlock",
    "InstructionResolver",
    "InstructionSource",
    "add_workspace_instruction",
    "detect_project_root",
    "get_instruction_resolver",
    "read_workspace_auto_discover",
    "read_workspace_instructions",
    "remove_workspace_instruction",
    "reset_instruction_resolver",
    "workspace_instructions_path",
    "write_workspace_auto_discover",
    "write_workspace_instructions",
]
