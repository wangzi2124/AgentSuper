"""Markdown slash commands with YAML frontmatter (opencode gap G-02).

Commands live in ``~/.config/agentsuper/commands/*.md`` (global) and
``<workspace>/.agentsuper/commands/*.md`` (project). Project wins by name.

Body placeholders:

* ``$ARGUMENTS`` — everything the user typed after the command
* ``$1``..``$9`` — shell-like positional arguments
* ``@path`` — inline file contents (relative to the session workspace)
* ``!`cmd``` — shell interpolation. It is **not** executed by default: a caller
  must supply an approved ``shell_runner`` (the runtime routes it through the
  normal permission/approval gate). Without a runner the literal stays intact,
  so command text can never become an implicit permission bypass.

Frontmatter may also carry ``template`` (overrides the Markdown body as the
template) and ``subtask`` / ``subagent`` (run the command as a subagent task).
"""

from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

logger = logging.getLogger(__name__)

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_FILE_REF = re.compile(r"(?<![\w/])@([\w][\w./\\-]*)")
_SHELL_REF = re.compile(r"!`([^`]+)`")

# Frontmatter keys are passed through to the model; never let them become a
# shell command or override internal fields.
_RESERVED_FRONTMATTER = {"name", "description", "template", "agent", "model", "subtask", "subagent", "files"}


@dataclass(frozen=True)
class CommandDef:
    name: str
    description: str = ""
    template: str = ""
    agent: str = ""
    model: str = ""
    subtask: bool = False
    files: tuple[str, ...] = ()
    source: str = "global"
    path: str = ""

    def render(
        self,
        arguments: str,
        workspace: str | Path | None = None,
        shell_runner: Any | None = None,
    ) -> str:
        args = _split_args(arguments)
        text = self.template or "$ARGUMENTS"
        text = text.replace("$ARGUMENTS", arguments or "")
        for index in range(9, 0, -1):
            value = args[index - 1] if index <= len(args) else ""
            text = text.replace(f"${index}", value)
        text = _inline_files(text, workspace)
        text = _append_globs(text, self.files, workspace)
        text = _expand_shell(text, shell_runner, workspace)
        if not text.strip():
            text = "（用户只输入了命令，未附带具体请求。请先按该命令的工作流，向用户确认必要信息。）"
        header = f"[命令：{self.name}]"
        if self.agent:
            header += f"（Agent：{self.agent}）"
        if self.subtask:
            header += "（子任务）"
        return f"{header}\n{text.strip()}\n[命令结束]"


def _split_args(arguments: str) -> list[str]:
    try:
        return shlex.split(arguments or "")
    except ValueError:
        return (arguments or "").split()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return ""


def _inline_files(text: str, workspace: str | Path | None) -> str:
    root = Path(workspace) if workspace else None
    if root is None:
        return text

    def replace(match: re.Match[str]) -> str:
        raw = match.group(1)
        target = (root / raw).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            return match.group(0)
        body = _read_text(target)
        if not body:
            return match.group(0)
        return f'\n<file path="{raw}">\n{body}\n</file>\n'

    return _FILE_REF.sub(replace, text)


def _append_globs(text: str, patterns: Iterable[str], workspace: str | Path | None) -> str:
    if not workspace:
        return text
    root = Path(workspace)
    chunks: list[str] = [text]
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            body = _read_text(path)
            if not body:
                continue
            rel = path.relative_to(root).as_posix()
            chunks.append(f'\n<file path="{rel}">\n{body}\n</file>\n')
    return "".join(chunks)


def _expand_shell(
    text: str,
    runner: Any | None,
    workspace: str | Path | None,
) -> str:
    """Expand ``!`cmd``` only when an approved runner is supplied.

    The runner is expected to have already passed the shell permission gate.
    Any error is surfaced inline so the model can explain the failure rather
    than silently dropping the command.
    """
    if runner is None or not _SHELL_REF.search(text):
        return text

    def replace(match: re.Match[str]) -> str:
        command = match.group(1).strip()
        if not command:
            return ""
        try:
            output = runner(command, workspace)
        except Exception as exc:  # noqa: BLE001 - never break command rendering
            return f"\n<shell command={command!r} status=\"error\">\n{exc}\n</shell>\n"
        return f"\n<shell command={command!r}>\n{str(output).strip()}\n</shell>\n"

    return _SHELL_REF.sub(replace, text)


def parse_command_file(path: Path, source: str, root: Path | None = None) -> CommandDef | None:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        logger.warning("cannot read command %s: %s", path, exc)
        return None
    meta: dict[str, Any] = {}
    body = raw
    match = _FRONTMATTER.match(raw)
    if match:
        try:
            loaded = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            logger.warning("invalid command frontmatter in %s: %s", path, exc)
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
    files = meta.get("files") or ()
    if isinstance(files, str):
        files = [part.strip() for part in files.split(",") if part.strip()]
    elif isinstance(files, (list, tuple)):
        files = [str(part).strip() for part in files if str(part).strip()]
    else:
        files = ()
    template = meta.get("template")
    if not isinstance(template, str) or not template.strip():
        template = body.strip()
    subtask = bool(meta.get("subtask") or meta.get("subagent") or False)
    return CommandDef(
        name=name,
        description=str(meta.get("description") or "").strip(),
        template=template,
        agent=str(meta.get("agent") or "").strip(),
        model=str(meta.get("model") or "").strip(),
        subtask=subtask,
        files=tuple(files),
        source=source,
        path=str(path),
    )


class CommandRegistry:
    def __init__(self, commands: Iterable[CommandDef] | None = None) -> None:
        self._commands: dict[str, CommandDef] = {}
        for command in commands or []:
            self.register(command)

    def register(self, command: CommandDef) -> None:
        self._commands[command.name.lower()] = command

    def get(self, name: str) -> CommandDef | None:
        return self._commands.get(str(name or "").strip().lower())

    def list(self) -> list[CommandDef]:
        return sorted(
            self._commands.values(),
            key=lambda command: (command.source != "project", command.name),
        )

    def __len__(self) -> int:
        return len(self._commands)


def command_dirs(workspace: str | Path | None) -> list[tuple[str, Path]]:
    dirs: list[tuple[str, Path]] = [
        ("global", Path.home() / ".config" / "agentsuper" / "commands"),
    ]
    if workspace:
        dirs.append(("project", Path(workspace).expanduser() / ".agentsuper" / "commands"))
    return dirs


def load_commands(workspace: str | Path | None = None) -> CommandRegistry:
    registry = CommandRegistry()
    for source, directory in command_dirs(workspace):
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.md")):
            command = parse_command_file(path, source, root=directory)
            if command is not None:
                registry.register(command)
    return registry


__all__ = [
    "CommandDef",
    "CommandRegistry",
    "command_dirs",
    "load_commands",
    "parse_command_file",
]
