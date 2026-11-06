"""Turn/Tool Part lifecycle.

This module is the single source of truth for what happened inside one tool
loop step.  Adapters, the graph loop, SSE projection, and tests all observe the
same ``ToolPart`` objects instead of re-deriving state from ad-hoc lists.

The important invariant is deliberately small::

    finish_reason == "stop" and no Tool Parts -> terminal
    finish_reason == "stop" and Tool Parts    -> continue

A provider may report ``stop`` even after emitting tool calls (weak/fallback
models do this).  The presence of parts, not the provider's finish label,
decides whether the loop still has work to do.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .contracts import ToolPartProjection


class ToolPartStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


def _as_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:  # noqa: BLE001 - malformed arguments are handled later
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return str(value)


def _target_from_arguments(arguments: dict[str, Any]) -> str:
    for key in (
        "path",
        "file_path",
        "filepath",
        "filePath",
        "file",
        "dir",
        "directory",
        "cwd",
        "command",
        "pattern",
    ):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


@dataclass
class ToolPart:
    """One tool call and its lifecycle state."""

    call_id: str
    name: str
    agent_id: str
    step_index: int
    step_id: str = ""
    status: ToolPartStatus = ToolPartStatus.PENDING
    raw_arguments: str = "{}"
    arguments: dict[str, Any] = field(default_factory=dict)
    output: str = ""
    duration_ms: float | None = None
    target: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        if not self.step_id:
            self.step_id = f"tool-{self.call_id}"
        if not self.arguments:
            self.arguments = _as_arguments(self.raw_arguments)
        if not self.target:
            self.target = _target_from_arguments(self.arguments)

    @property
    def is_open(self) -> bool:
        return self.status in (ToolPartStatus.PENDING, ToolPartStatus.RUNNING)

    @property
    def is_finished(self) -> bool:
        return self.status in (ToolPartStatus.COMPLETED, ToolPartStatus.FAILED)

    def start(self, arguments: dict[str, Any] | None = None) -> None:
        if arguments is not None:
            self.arguments = dict(arguments)
        self.raw_arguments = _as_text(self.arguments) or "{}"
        self.target = _target_from_arguments(self.arguments)
        self.status = ToolPartStatus.RUNNING

    def finish(
        self,
        output: Any,
        duration_ms: float | None = None,
        *,
        failed: bool = False,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        if arguments is not None:
            self.arguments = dict(arguments)
            self.raw_arguments = _as_text(self.arguments) or "{}"
            self.target = _target_from_arguments(self.arguments)
        self.output = _as_text(output)
        self.duration_ms = duration_ms
        self.status = ToolPartStatus.FAILED if failed else ToolPartStatus.COMPLETED
        if failed:
            self.error = self.output

    def call_tuple(self) -> tuple[str, str, str]:
        return (self.call_id, self.name, self.raw_arguments or "{}")

    def projection(self, event_type: str) -> ToolPartProjection:
        return ToolPartProjection(
            type=event_type,
            part_id=self.call_id,
            step_id=self.step_id,
            agent_id=self.agent_id,
            agent_name=self.agent_id,
            name=self.name,
            status=self.status.value,
            tool_args=dict(self.arguments),
            tool_result=self.output if self.is_finished else None,
            duration_ms=self.duration_ms,
            error=self.error or None,
        )

    def event(self, event_type: str) -> dict[str, Any]:
        return self.projection(event_type).to_event()

    def trace(self) -> dict[str, Any]:
        return {"name": self.name, "args": dict(self.arguments), "output": self.output}


@dataclass
class TurnStep:
    """One model step and all Tool Parts produced by it."""

    index: int
    content: str = ""
    finish_reason: str = "stop"
    parts: list[ToolPart] = field(default_factory=list)
    results_fed: bool = False

    def part(self, call_id: str) -> ToolPart | None:
        for part in self.parts:
            if part.call_id == call_id:
                return part
        return None

    @property
    def has_parts(self) -> bool:
        return bool(self.parts)

    @property
    def has_open_parts(self) -> bool:
        return any(part.is_open for part in self.parts)


class TurnPartStore:
    """State machine for one ``run_tool_loop`` turn.

    The store owns part lifecycle and the continuation decision.  It deliberately
    knows nothing about LangGraph or SSE; callers pass an ``emit`` callback.
    """

    def __init__(
        self,
        agent_id: str,
        *,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self._emit = emit or (lambda _event: None)
        self.steps: list[TurnStep] = []
        self._by_call: dict[str, ToolPart] = {}
        self._current: TurnStep | None = None
        self.finish_reason = "stop"

    @property
    def current_step(self) -> TurnStep | None:
        return self._current

    def begin_step(self) -> TurnStep:
        if self._current is not None:
            return self._current
        step = TurnStep(index=len(self.steps))
        self.steps.append(step)
        self._current = step
        return step

    def abort_step(self) -> None:
        """Drop an unfinished model attempt (overflow/retry)."""
        step = self._current
        if step is None:
            return
        for part in step.parts:
            if part.is_open:
                part.status = ToolPartStatus.FAILED
                part.error = "aborted"
                part.output = "aborted"
        if not step.parts:
            self.steps.remove(step)
        self._current = None

    def record_tool_call(
        self,
        call_id: str,
        name: str,
        raw_arguments: Any = "{}",
        *,
        arguments: dict[str, Any] | None = None,
    ) -> ToolPart:
        step = self.begin_step()
        existing = self._by_call.get(call_id)
        if existing is not None:
            if existing not in step.parts:
                step.parts.append(existing)
                existing.step_index = step.index
            return existing
        raw = _as_text(raw_arguments) or "{}"
        part = ToolPart(
            call_id=call_id,
            name=name,
            agent_id=self.agent_id,
            step_index=step.index,
            raw_arguments=raw,
            arguments=dict(arguments or _as_arguments(raw)),
        )
        step.parts.append(part)
        self._by_call[call_id] = part
        return part

    def part(self, call_id: str) -> ToolPart | None:
        return self._by_call.get(call_id)

    def start_tool(
        self,
        part: ToolPart,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        part.start(arguments)
        self._emit(part.event("tool_start"))

    def finish_tool(
        self,
        part: ToolPart,
        output: Any,
        *,
        duration_ms: float | None = None,
        failed: bool = False,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        part.finish(
            output,
            duration_ms,
            failed=failed,
            arguments=arguments,
        )
        self._emit(part.event("tool_end"))

    def record_result(
        self,
        result: Any,
        *,
        finish_reason: str | None = None,
    ) -> TurnStep:
        step = self.begin_step()
        step.content = str(getattr(result, "content", "") or "")
        step.finish_reason = finish_reason or str(
            getattr(result, "finish_reason", "stop") or "stop"
        )
        self.finish_reason = step.finish_reason
        for call in getattr(result, "tool_calls", None) or []:
            call_id = str(getattr(call, "id", "") or "")
            name = str(getattr(call, "name", "") or "")
            raw = getattr(call, "arguments", "")
            if call_id and name:
                self.record_tool_call(call_id, name, raw)
        return step

    def finish_step(self, step: TurnStep | None = None) -> None:
        step = step or self._current
        if step is None:
            return
        step.results_fed = True
        if self._current is step:
            self._current = None

    def should_continue(self, step: TurnStep | None = None) -> bool:
        """Provider finish labels are advisory; Tool Parts are the contract."""
        step = step or self._current
        if step is None:
            return False
        if step.finish_reason in ("content-filter", "content_filter", "error"):
            return False
        return step.has_parts

    @property
    def is_terminal(self) -> bool:
        return not self.should_continue()

    def completed_parts(self) -> list[ToolPart]:
        return [
            part
            for step in self.steps
            for part in step.parts
            if part.status is ToolPartStatus.COMPLETED
        ]

    def tool_trace(self) -> list[dict[str, Any]]:
        return [part.trace() for step in self.steps for part in step.parts if part.is_finished]

    def tool_history(self) -> list[dict[str, Any]]:
        from .tool_history import make_tool_call_message, make_tool_result_message

        history: list[dict[str, Any]] = []
        for step in self.steps:
            parts = [part for part in step.parts if part.is_finished]
            if not parts:
                continue
            history.append(
                make_tool_call_message([part.call_tuple() for part in parts], step.content)
            )
            for part in parts:
                history.append(
                    make_tool_result_message(part.call_id, part.name, part.output)
                )
        return history
