"""Skill source discovery (opencode gap G-17)."""

from __future__ import annotations

from pathlib import Path


def global_skill_dirs() -> list[str]:
    home = Path.home()
    candidates = [
        home / ".claude" / "skills",
        home / ".agents" / "skills",
        home / ".config" / "agentsuper" / "skills-external",
    ]
    return [str(path) for path in candidates if path.is_dir()]


def workspace_skill_dirs(workspace: str | Path | None) -> list[str]:
    if not workspace:
        return []
    root = Path(workspace).expanduser()
    candidates = [
        root / ".claude" / "skills",
        root / ".agents" / "skills",
        root / ".agentsuper" / "skills",
    ]
    return [str(path) for path in candidates if path.is_dir()]


def merge_dirs(existing: list[str], extra: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in [*existing, *extra]:
        try:
            key = str(Path(item).expanduser().resolve())
        except OSError:
            key = str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(str(Path(item).expanduser()))
    return out


__all__ = ["global_skill_dirs", "merge_dirs", "workspace_skill_dirs"]
