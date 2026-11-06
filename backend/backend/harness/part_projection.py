"""Canonical Tool Part projection and its two adapters.

The graph emits canonical :class:`~harness.contracts.ToolPartProjection` fields.
This module turns that one shape into:

* live SSE events (``agent_start`` / ``agent_step`` / ``agent_done``), and
* the persisted ``agents`` snapshot used for replay after refresh.

Keeping both in one projector removes the previous effective-args repair in the
SSE adapter: the Tool Part already carries the arguments that were executed.
"""

from __future__ import annotations

from typing import Any

from .contracts import ToolPartProjection

_AGENT_EVENT_TYPES = {"agent_start", "agent_step", "agent_done", "agent_error"}


class AgentPartProjector:
    """Project canonical Tool Parts into live SSE and persisted replay shapes."""

    def __init__(
        self,
        seen: list[str] | None = None,
        agents: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.seen = seen if seen is not None else []
        self.agents = agents if agents is not None else {}

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self.agents.values())

    def track(self, event: dict[str, Any]) -> None:
        """Fold an ``agent_*`` event into the replay snapshot."""
        etype = event.get("type")
        agent_id = event.get("agent_id")
        if etype not in _AGENT_EVENT_TYPES or not agent_id:
            return
        agent = self.agents.get(agent_id)
        if agent is None:
            agent = {
                "agent_id": agent_id,
                "agent_name": event.get("agent_name") or agent_id,
                "agent_avatar": event.get("agent_avatar") or "",
                "status": "running",
                "content": "",
                "steps": [],
            }
            self.agents[agent_id] = agent
        if event.get("agent_name"):
            agent["agent_name"] = event["agent_name"]
        if event.get("agent_avatar"):
            agent["agent_avatar"] = event["agent_avatar"]
        if etype == "agent_step":
            step = event.get("step") or {}
            sid = step.get("step_id") or step.get("part_id")
            if not sid:
                return
            for index, old in enumerate(agent["steps"]):
                if (old.get("step_id") or old.get("part_id")) == sid:
                    agent["steps"][index] = {**old, **step}
                    return
            agent["steps"].append(step)
        elif etype == "agent_done":
            agent["status"] = "completed"
            if event.get("content"):
                agent["content"] = event["content"]
        elif etype == "agent_error":
            agent["status"] = "failed"
            if event.get("error"):
                agent["error"] = event["error"]

    def start_agent(
        self,
        agent_id: str,
        agent_name: str,
        agent_avatar: str = "",
    ) -> dict[str, Any] | None:
        agent_id = str(agent_id or "build")
        if agent_id in self.seen:
            return None
        self.seen.append(agent_id)
        event = {
            "type": "agent_start",
            "agent_id": agent_id,
            "agent_name": str(agent_name or agent_id),
            "agent_avatar": str(agent_avatar or ""),
        }
        self.track(event)
        return event

    def _ensure_started(self, event: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        agent_id = str(event.get("agent_id") or "build")
        started = self.start_agent(
            agent_id,
            str(event.get("agent_name") or agent_id),
            str(event.get("agent_avatar") or ""),
        )
        return agent_id, ([started] if started else [])

    def project(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """Project one graph event into zero or more live SSE events."""
        etype = event.get("type")
        if etype == "text_delta":
            delta = event.get("delta")
            if not delta:
                return []
            return [
                {
                    "type": "text_delta",
                    "agent_id": str(event.get("agent_id") or ""),
                    "delta": str(delta),
                }
            ]
        if etype in ("tool_start", "tool_end"):
            projection = ToolPartProjection.from_event(event)
            if projection is None:
                return []
            agent_id, out = self._ensure_started(event)
            step = projection.to_step()
            step["agent_id"] = agent_id
            step_event = {
                "type": "agent_step",
                "agent_id": agent_id,
                "agent_name": str(event.get("agent_name") or agent_id),
                "step": step,
            }
            self.track(step_event)
            return out + [step_event]
        if etype == "context_truncated":
            agent_id, out = self._ensure_started(event)
            return out + [
                {
                    "type": "context_truncated",
                    "agent_id": agent_id,
                    "agent_name": str(event.get("agent_name") or agent_id),
                    "model": event.get("model"),
                    "window": event.get("window"),
                    "budget": event.get("budget"),
                    "reserve": event.get("reserve"),
                    "estimated_before": event.get("estimated_before"),
                    "estimated_after": event.get("estimated_after"),
                    "dropped_messages": event.get("dropped_messages"),
                }
            ]
        if etype == "agent_start":
            _, out = self._ensure_started(event)
            return out
        if etype == "agent_end":
            agent_id, out = self._ensure_started(event)
            failed = str(event.get("status") or "").lower() in ("failed", "error")
            done_event = {
                "type": "agent_error" if failed else "agent_done",
                "agent_id": agent_id,
                "agent_name": str(event.get("agent_name") or agent_id),
            }
            if failed and event.get("error"):
                done_event["error"] = str(event["error"])
            if event.get("content"):
                done_event["content"] = str(event["content"])
            self.track(done_event)
            return out + [done_event]
        return []


__all__ = ["AgentPartProjector"]
