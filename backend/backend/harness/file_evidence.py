"""File-task evidence, expressed as a query over Tool Parts.

The loop must not accept an answer about a directory or file unless a Tool Part
actually produced evidence for *that* target.  A prior ``ls`` of a parent
directory is not evidence that a file was read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from .turn_parts import ToolPart, ToolPartStatus, _as_text

_FILE_TASK_RE = re.compile(
    r"(?:列出|查看|看看|看|显示|读取|打开|read|ls|dir|find|grep|glob)"
    r".{0,24}(?:文件|目录|文件夹|路径|盘|[\w./-]+\.\w{1,8})"
    r"|(?:文件|目录|文件夹|盘).{0,24}(?:有哪些|有什么|列表|内容|结构)",
    re.IGNORECASE,
)
_FILE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,8})(?![A-Za-z0-9_.-])"
)
_PATH_TOKEN_RE = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s，。；;、]+"
    r"|(?:\.{1,2}[\\/])?[A-Za-z0-9_.-]+(?:[\\/][A-Za-z0-9_.-]+)+)"
)
_DIR_BEFORE_RE = re.compile(
    r"([A-Za-z0-9_\-\u4e00-\u9fff]{2,})\s*(?:目录|文件夹)"
)
_DIR_AFTER_RE = re.compile(
    r"(?:目录|文件夹|路径)(?:下|里|中)?\s*[：:]?\s*"
    r"([A-Za-z0-9_\-\u4e00-\u9fff]{2,})"
)
_COMMAND_TARGET_RE = re.compile(
    r"(?:^|\s)(?:ls|dir|read|cat|type|find|grep|glob)\s+([^\s，。；;、]+)",
    re.IGNORECASE,
)
_DRIVE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z])盘")
_GENERIC_DIR_NAMES = frozenset(
    {
        "这个",
        "那个",
        "该",
        "此",
        "当前",
        "上述",
        "一个",
        "所在",
        "其",
        "它",
        "所有",
        "全部",
        "本",
    }
)
_DIR_VERB_PREFIXES = (
    "列出",
    "列一下",
    "查看",
    "看看",
    "看",
    "显示",
    "读取",
    "打开",
    "搜索",
    "查找",
)
_CUSTOM_FILE_TOOL_RE = re.compile(
    r"(?:read|file|list|ls|dir|find|grep|glob|cat|open|search)",
    re.IGNORECASE,
)

_READ_ACTION_RE = re.compile(
    r"(?:读取|查看|看看|打开|内容|总结|分析|是什么|有什么用|写了什么|\bread\b|\bcat\b|\btype\b)",
    re.IGNORECASE,
)
_LIST_ACTION_RE = re.compile(
    r"(?:列出|列一下|列目录|有哪些|有什么|目录|文件夹|结构|清单|\bls\b|\bdir\b)",
    re.IGNORECASE,
)
_SEARCH_ACTION_RE = re.compile(
    r"(?:搜索|查找|搜一下|\bgrep\b|包含|匹配|\bfindstr\b|\bfind\b)",
    re.IGNORECASE,
)
ACTION_PROMISE_RE = re.compile(
    r"(?:我(?:会|将|马上|这就|现在)|请稍等|稍等|让我|接下来|正在|准备)"
    r"[^\n。！？!?]{0,24}"
    r"(?:读取|查看|打开|列出|搜索|检查|执行|调用|看一下|看看)",
    re.IGNORECASE,
)

_ACTION_TOOL_NAMES: dict[str, frozenset[str]] = {
    "read": frozenset({"read_file", "grep", "glob", "shell_exec"}),
    "list": frozenset({"ls", "read_file", "glob", "shell_exec"}),
    "search": frozenset({"grep", "glob", "shell_exec"}),
}
_ALL_FILE_TOOLS = frozenset(
    {
        "ls",
        "read_file",
        "grep",
        "glob",
        "shell_exec",
        "write_file",
        "edit_file",
        "append_file",
        "delete_file",
        "create_directory",
        "task",
    }
)


def _normalize_target(value: str) -> str:
    text = str(value or "").strip().strip("\"'`")
    text = text.replace("\\", "/")
    while len(text) > 1 and text.endswith("/"):
        text = text[:-1]
    return text.lower()


def _target_matches(part: ToolPart, target: str) -> bool:
    actual = _normalize_target(part.target)
    expected = _normalize_target(target)
    if not expected:
        return True
    if not actual:
        return False
    if actual == expected:
        return True
    if part.name == "shell_exec" and expected in actual:
        return True
    actual_base = actual.rsplit("/", 1)[-1]
    expected_base = expected.rsplit("/", 1)[-1]
    return bool(actual_base) and actual_base == expected_base


def _clean_dir_candidate(candidate: str) -> str:
    value = candidate.strip()
    for prefix in _DIR_VERB_PREFIXES:
        if value.startswith(prefix) and len(value) > len(prefix):
            value = value[len(prefix):].strip()
            break
    return "" if value in _GENERIC_DIR_NAMES else value


def _extract_target(text: str) -> str:
    files = _FILE_TOKEN_RE.findall(text)
    if files:
        return files[-1]
    paths = _PATH_TOKEN_RE.findall(text)
    if paths:
        return paths[-1]
    drive = _DRIVE_RE.search(text)
    if drive:
        return f"{drive.group(1)}:"
    command = _COMMAND_TARGET_RE.search(text)
    if command:
        return command.group(1)
    for match in reversed(list(_DIR_BEFORE_RE.finditer(text))):
        candidate = _clean_dir_candidate(match.group(1))
        if candidate:
            return candidate
    after = _DIR_AFTER_RE.search(text)
    if after:
        candidate = _clean_dir_candidate(after.group(1))
        if candidate:
            return candidate
    return ""


def _extract_actions(text: str) -> frozenset[str]:
    actions: set[str] = set()
    if _READ_ACTION_RE.search(text):
        actions.add("read")
    if _LIST_ACTION_RE.search(text):
        actions.add("list")
    if _SEARCH_ACTION_RE.search(text):
        actions.add("search")
    return frozenset(actions)


@dataclass(frozen=True)
class FileTaskRequirement:
    actions: frozenset[str]
    target: str = ""
    raw: str = ""

    def matches(self, part: ToolPart) -> bool:
        if part.status is not ToolPartStatus.COMPLETED:
            return False
        if self.actions:
            allowed = set()
            for action in self.actions:
                allowed.update(_ACTION_TOOL_NAMES.get(action, ()))
            if part.name not in allowed:
                if not (
                    self.target
                    and _CUSTOM_FILE_TOOL_RE.search(part.name)
                    and _target_matches(part, self.target)
                ):
                    return False
        elif part.name not in _ALL_FILE_TOOLS:
            if not (
                self.target
                and _CUSTOM_FILE_TOOL_RE.search(part.name)
                and _target_matches(part, self.target)
            ):
                return False
        return _target_matches(part, self.target)


def parse_file_task(text: str) -> FileTaskRequirement | None:
    value = str(text or "")
    match = _FILE_TASK_RE.search(value)
    if match is None:
        return None
    return FileTaskRequirement(
        actions=_extract_actions(value),
        target=_extract_target(value),
        raw=match.group(0),
    )


def has_evidence(
    parts: Iterable[ToolPart],
    requirement: FileTaskRequirement | None,
) -> bool:
    if requirement is None:
        return True
    return any(requirement.matches(part) for part in parts)


def _call_arguments(messages: list[Any]) -> dict[str, dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") or {}
            arguments = fn.get("arguments") if isinstance(fn, dict) else None
            if isinstance(arguments, str):
                try:
                    parsed = json.loads(arguments)
                except Exception:  # noqa: BLE001
                    parsed = {}
            elif isinstance(arguments, dict):
                parsed = arguments
            else:
                parsed = {}
            call_id = str(call.get("id") or "")
            if call_id:
                calls[call_id] = parsed if isinstance(parsed, dict) else {}
    return calls


def has_prior_evidence(
    messages: list[Any],
    requirement: FileTaskRequirement | None,
) -> bool:
    if requirement is None:
        return True
    args_by_call = _call_arguments(messages)
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        call_id = str(message.get("tool_call_id") or "")
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        head = content[:120].lower()
        if head.startswith(("error:", "❌")) or any(
            marker in head for marker in ("permission denied", "not found", "拒绝", "无权")
        ):
            continue
        name = str(message.get("name") or "")
        args = args_by_call.get(call_id, {})
        target = ""
        for key in ("path", "file_path", "filepath", "filePath", "file", "dir", "directory", "cwd", "command", "pattern"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                target = value.strip()
                break
        part = ToolPart(
            call_id=call_id or "prior",
            name=name,
            agent_id="",
            step_index=0,
            arguments=args,
            target=target,
        )
        part.status = ToolPartStatus.COMPLETED
        if requirement.matches(part):
            return True
    return False


class FileEvidenceDecision(str, Enum):
    SATISFIED = "satisfied"
    NUDGE = "nudge"
    BLOCK = "block"


@dataclass(frozen=True)
class FileTaskEvidence:
    """One file-task policy decision, queried at the loop's step boundary."""

    requirement: FileTaskRequirement | None = None
    prior_evidence: bool = False

    @classmethod
    def from_messages(cls, messages: list[Any]) -> FileTaskEvidence:
        text = ""
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") in ("user", "human"):
                text = _as_text(message.get("content"))
                break
        requirement = parse_file_task(text)
        return cls(
            requirement=requirement,
            prior_evidence=has_prior_evidence(messages, requirement),
        )

    def decide(
        self,
        parts: Iterable[ToolPart],
        answer: str,
        *,
        can_nudge: bool = False,
    ) -> FileEvidenceDecision:
        if self.requirement is None or self.prior_evidence:
            return FileEvidenceDecision.SATISFIED
        if has_evidence(parts, self.requirement):
            return FileEvidenceDecision.SATISFIED
        if can_nudge and ACTION_PROMISE_RE.search(str(answer or "")):
            return FileEvidenceDecision.NUDGE
        return FileEvidenceDecision.BLOCK

