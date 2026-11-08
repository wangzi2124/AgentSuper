"""Markdown agent definitions with YAML frontmatter (opencode gap G-05).

Discovery order (later overrides earlier by name):

1. builtin:  ``backend/agents/*.md``
2. global:   ``~/.config/agentsuper/agents/*.md``
3. project:  ``<workspace>/.agentsuper/agents/*.md``
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

import yaml

logger = logging.getLogger(__name__)

VALID_MODES = {"primary", "subagent", "all"}
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass(frozen=True)
class AgentDef:
    name: str
    description: str = ""
    mode: str = "subagent"
    tier: str = "medium"
    model: str = ""
    tools: tuple[str, ...] = ()
    permission: dict[str, str] = field(default_factory=dict)
    request: dict[str, Any] = field(default_factory=dict)
    max_rounds: int = 6
    hidden: bool = False
    disabled: bool = False
    color: str = ""
    source: str = "builtin"
    path: str = ""
    prompt_path: str = ""
    prompt_body: str = ""

    @property
    def is_primary(self) -> bool:
        return self.mode in ("primary", "all")

    @property
    def is_subagent(self) -> bool:
        return self.mode in ("subagent", "all")

    def system_prompt(self) -> str:
        """Render the agent prompt (Markdown body wins over prompt_path)."""
        if self.prompt_body.strip():
            return self.prompt_body.strip()
        if self.prompt_path:
            try:
                from prompts.loader import PromptLoader

                agent_type = self.prompt_path.rsplit("/", 1)[-1].removesuffix(".md")
                return PromptLoader().build_agent_prompt(agent_type)
            except Exception as exc:  # noqa: BLE001
                logger.warning("cannot load agent prompt %s: %s", self.prompt_path, exc)
        return self.description or f"You are the {self.name} agent."


def _as_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, (list, tuple)):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return ()


def parse_agent_file(path: Path, source: str, root: Path | None = None) -> AgentDef | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        logger.warning("cannot read agent file %s: %s", path, exc)
        return None

    meta: dict[str, Any] = {}
    body = text
    match = _FRONTMATTER.match(text)
    if match:
        try:
            loaded = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            logger.warning("invalid agent frontmatter in %s: %s", path, exc)
            return None
        if isinstance(loaded, dict):
            meta = loaded
        body = match.group(2)

    name = str(meta.get("name") or path.stem).strip()
    if "name" not in meta and root is not None:
        try:
            name = path.relative_to(root).with_suffix("").as_posix()
        except ValueError:
            name = path.stem
    if not name:
        return None
    mode = str(meta.get("mode") or "subagent").strip().lower()
    if mode not in VALID_MODES:
        mode = "subagent"
    try:
        max_rounds = int(
            meta.get("max_rounds") or meta.get("maxRounds") or meta.get("steps") or 6
        )
    except (TypeError, ValueError):
        max_rounds = 6
    permission = meta.get("permission") or meta.get("permissions") or {}
    if not isinstance(permission, dict):
        permission = {}
    request = meta.get("request") or {}
    if not isinstance(request, dict):
        request = {}

    return AgentDef(
        name=name,
        description=str(meta.get("description") or "").strip(),
        mode=mode,
        tier=str(meta.get("tier") or "medium").strip(),
        model=str(meta.get("model") or "").strip(),
        tools=_as_list(meta.get("tools")),
        permission={str(k): str(v) for k, v in permission.items() if isinstance(v, (str, int, float, bool))},
        request=request,
        max_rounds=max(1, max_rounds),
        hidden=bool(meta.get("hidden") or False),
        disabled=bool(meta.get("disabled") or False),
        color=str(meta.get("color") or "").strip(),
        source=source,
        path=str(path),
        prompt_path=str(meta.get("prompt") or meta.get("prompt_path") or "").strip(),
        prompt_body=body,
    )


class AgentRegistry:
    """Ordered agent registry; later sources override earlier ones by name."""

    def __init__(self, agents: Iterable[AgentDef] | None = None) -> None:
        self._agents: dict[str, AgentDef] = {}
        for agent in agents or []:
            self.register(agent)

    def register(self, agent: AgentDef) -> None:
        key = agent.name.lower()
        existing = self._agents.get(key)
        if existing is None:
            self._agents[key] = agent
            return
        merged_request = dict(existing.request)
        merged_request.update(agent.request)
        merged_permission = dict(existing.permission)
        merged_permission.update(agent.permission)
        self._agents[key] = replace(
            existing,
            description=agent.description or existing.description,
            mode=agent.mode,
            tier=agent.tier or existing.tier,
            model=agent.model or existing.model,
            tools=agent.tools or existing.tools,
            permission=merged_permission,
            request=merged_request,
            max_rounds=agent.max_rounds,
            hidden=agent.hidden,
            disabled=agent.disabled,
            color=agent.color or existing.color,
            source=agent.source,
            path=agent.path,
            prompt_path=agent.prompt_path or existing.prompt_path,
            prompt_body=agent.prompt_body or existing.prompt_body,
        )

    def get(self, name: str) -> AgentDef | None:
        return self._agents.get(str(name or "").strip().lower())

    def list(
        self,
        *,
        include_hidden: bool = False,
        include_disabled: bool = False,
        mode: str | None = None,
    ) -> list[AgentDef]:
        items = [
            agent
            for agent in self._agents.values()
            if (include_hidden or not agent.hidden)
            and (include_disabled or not agent.disabled)
        ]
        if mode == "primary":
            items = [agent for agent in items if agent.is_primary]
        elif mode == "subagent":
            items = [agent for agent in items if agent.is_subagent]
        return sorted(items, key=lambda agent: (agent.source != "project", agent.name))

    def subagents(self) -> dict[str, AgentDef]:
        return {agent.name: agent for agent in self.list(mode="subagent")}

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, name: object) -> bool:
        return str(name).lower() in self._agents


def _agent_dirs(workspace: str | Path | None) -> list[tuple[str, Path]]:
    dirs: list[tuple[str, Path]] = [
        ("builtin", Path(__file__).resolve().parent),
        ("global", Path.home() / (".config") / "agentsuper" / "agents"),
    ]
    if workspace:
        dirs.append(("project", Path(workspace).expanduser() / ".agentsuper" / "agents"))
    return dirs


def _target_dir(workspace: str | Path | None, scope: str) -> Path:
    if scope == "project":
        if not workspace:
            raise ValueError("project scope requires a workspace")
        return Path(workspace).expanduser() / ".agentsuper" / "agents"
    return Path.home() / ".config" / "agentsuper" / "agents"


def _safe_relpath(name: str) -> Path:
    parts = [part for part in str(name or "").split("/") if part not in ("", ".", "..")]
    if not parts:
        raise ValueError("agent id is required")
    return Path(*parts).with_suffix(".md")


def _dump_frontmatter(spec: dict[str, Any]) -> tuple[dict[str, Any], str]:
    meta: dict[str, Any] = {}
    mapping = {
        "description": "description",
        "mode": "mode",
        "tier": "tier",
        "model": "model",
        "hidden": "hidden",
        "disabled": "disabled",
        "color": "color",
        "prompt_path": "prompt",
    }
    for key, meta_key in mapping.items():
        value = spec.get(key)
        if value not in (None, "", False):
            meta[meta_key] = value
    if spec.get("tools"):
        meta["tools"] = list(spec["tools"])
    if spec.get("permission"):
        meta["permission"] = dict(spec["permission"])
    if spec.get("request"):
        meta["request"] = dict(spec["request"])
    steps = spec.get("max_rounds") or spec.get("steps")
    if steps:
        meta["max_rounds"] = int(steps)
    return meta, str(spec.get("prompt_body") or spec.get("system") or "")


def save_agent(spec: dict[str, Any], *, workspace: str | Path | None = None, scope: str = "global") -> AgentDef:
    name = str(spec.get("name") or spec.get("id") or "").strip()
    if not name:
        raise ValueError("agent name is required")
    directory = _target_dir(workspace, scope)
    path = directory / _safe_relpath(name)
    meta, body = _dump_frontmatter(spec)
    text = "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip() + "\n---\n"
    if body.strip():
        text += body.strip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    parsed = parse_agent_file(path, scope, root=directory)
    if parsed is None:
        raise ValueError(f"cannot parse saved agent: {path}")
    return parsed


def delete_agent(name: str, *, workspace: str | Path | None = None, scope: str = "project") -> bool:
    directory = _target_dir(workspace, scope)
    path = directory / _safe_relpath(name)
    if path.is_file():
        path.unlink()
        return True
    return False


def load_registry(workspace: str | Path | None = None) -> AgentRegistry:
    registry = AgentRegistry()
    for source, directory in _agent_dirs(workspace):
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.md")):
            agent = parse_agent_file(path, source, root=directory)
            if agent is not None:
                registry.register(agent)
    return registry


__all__ = [
    "AgentDef",
    "AgentRegistry",
    "VALID_MODES",
    "delete_agent",
    "load_registry",
    "parse_agent_file",
    "save_agent",
]
