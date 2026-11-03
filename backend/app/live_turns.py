"""Live-turn sidecars: crash-safe partial output for detached turns.

Graph checkpoints are the source of truth for chat history, but writing partial
assistant output into the same LangGraph thread while the graph is still running
would race with the graph's own checkpoints. Instead runners mirror partial
text/agent state into ``data/live_turns/*.json`` and normal completion deletes
the sidecar. On startup, leftovers are imported once as interrupted assistant
messages.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LIVE_DIR = "live_turns"
DEFAULT_THROTTLE_SEC = 2.0
_AGENT_EVENT_TYPES = {"agent_start", "agent_step", "agent_done", "agent_error"}


def live_dir() -> Path:
    from storage.paths import data_root

    path = data_root() / LIVE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe(name: str) -> str:
    """Filesystem-safe component: no separators, no dot-dot, no traversal."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(name or "unknown"))
    safe = safe.replace("..", "__").strip("._")
    return safe[:120] or "unknown"


def sidecar_path(session_id: str, turn_id: str) -> Path:
    return live_dir() / f"{_safe(session_id)}__{_safe(turn_id)}.json"


class LiveTurnRecorder:
    """Mirrors streamed partial output to a per-turn sidecar (throttled)."""

    def __init__(
        self,
        session_id: str,
        turn_id: str,
        *,
        throttle_sec: float = DEFAULT_THROTTLE_SEC,
    ) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self.path = sidecar_path(session_id, turn_id)
        self.text = ""
        self.agents: dict[str, dict[str, Any]] = {}
        self.assistant_msg_id: str | None = None
        self._throttle = max(0.0, float(throttle_sec))
        self._last_write = 0.0
        self._closed = False

    # ── event hooks ──────────────────────────────────────────────────────
    def reset(self) -> None:
        """Start recording a new drain iteration (one assistant message)."""
        self.text = ""
        self.agents = {}
        self.assistant_msg_id = None
        self._last_write = 0.0
        self._closed = False

    def observe(self, event: dict[str, Any]) -> None:
        if self._closed:
            # 回合已落库并清理：后续终态事件不得再写回 sidecar（否则重启误导入）。
            return
        etype = event.get("type")
        if etype == "text_delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                self.text += delta
        elif etype in _AGENT_EVENT_TYPES:
            self._track_agent(event)
        elif etype in ("done", "turn_done") and isinstance(event.get("answer"), str):
            # The terminal event carries the authoritative full answer.
            self.text = event["answer"]

        aggressive = etype in (
            "agent_step",
            "agent_done",
            "agent_error",
            "permission_request",
            "context_truncated",
            "done",
            "turn_done",
        )
        if aggressive or (time.time() - self._last_write >= self._throttle):
            self.flush()

    def note_assistant_id(self, message_id: str) -> None:
        self.assistant_msg_id = message_id

    def _track_agent(self, event: dict[str, Any]) -> None:
        agent_id = event.get("agent_id")
        etype = event.get("type")
        if not agent_id or etype not in _AGENT_EVENT_TYPES:
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
        if etype == "agent_step":
            step = event.get("step") or {}
            sid = step.get("step_id")
            if not sid:
                return
            for i, old in enumerate(agent["steps"]):
                if old.get("step_id") == sid:
                    agent["steps"][i] = {**old, **step}
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
        elif etype == "agent_start":
            agent["status"] = "running"

    # ── sidecar I/O ──────────────────────────────────────────────────────
    def flush(self, status: str = "running") -> None:
        if self._closed or (not self.text and not self.agents):
            return
        payload = {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "text": self.text,
            "agents": list(self.agents.values()),
            "assistant_msg_id": self.assistant_msg_id,
            "status": status,
            "updated_at": time.time(),
        }
        try:
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            self._last_write = time.time()
        except OSError:
            logger.debug("live-turn sidecar write failed: %s", self.path, exc_info=True)

    def clear(self) -> None:
        self._closed = True
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            logger.debug("live-turn sidecar delete failed: %s", self.path, exc_info=True)


def _existing_message_ids(state: Any) -> set[str]:
    out: set[str] = set()
    messages = state.get("messages") if isinstance(state, dict) else None
    if not isinstance(messages, list):
        return out
    for msg in messages:
        mid = getattr(msg, "id", None)
        if mid:
            out.add(str(mid))
    return out


async def reconcile_live_turns(ctx: Any) -> dict[str, int]:
    """Import leftover sidecars as interrupted assistant messages.

    Also flips any ``running`` session left behind by a previous process to
    ``interrupted`` and clears its queued inputs.
    """
    imported = 0
    skipped = 0
    for path in sorted(live_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - corrupt sidecar: drop it
            logger.warning("dropping unreadable live-turn sidecar: %s", path)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            continue

        session_id = str(data.get("session_id") or "")
        turn_id = str(data.get("turn_id") or "")
        text = str(data.get("text") or "").strip()
        assistant_msg_id = data.get("assistant_msg_id")

        session = ctx.sessions.get(session_id) if session_id else None
        if session_id and text and session is not None:
            try:
                state = await ctx.state(session_id)
                existing = _existing_message_ids(state)
                if assistant_msg_id and str(assistant_msg_id) in existing:
                    skipped += 1
                else:
                    from langchain_core.messages import AIMessage

                    message_id = str(assistant_msg_id or f"am_{_safe(turn_id)}")
                    msg = AIMessage(
                        content=text,
                        id=message_id,
                        additional_kwargs={
                            "ui": {
                                "agents": data.get("agents") or [],
                                "files_changed": [],
                                "partial_error": "服务重启，任务已中断",
                            }
                        },
                    )
                    await ctx.graph.aupdate_state(
                        {"configurable": {"thread_id": session_id}},
                        {"messages": [msg]},
                    )
                    imported += 1
            except Exception:  # noqa: BLE001 - reconciliation must not block startup
                logger.warning(
                    "live-turn import failed: session=%s turn=%s",
                    session_id,
                    turn_id,
                    exc_info=True,
                )

        if session_id and session is not None:
            try:
                from .session import repository as repo

                repo.update_session(session_id, status="interrupted")
                repo.clear_inputs(session_id)
            except Exception:  # noqa: BLE001
                logger.debug("session reconcile failed: %s", session_id, exc_info=True)

        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        from .session import repository as repo

        for info in repo.list_sessions_by_status("running"):
            try:
                repo.update_session(info.id, status="interrupted")
                repo.clear_inputs(info.id)
            except Exception:  # noqa: BLE001
                logger.debug("running-session reconcile failed: %s", info.id, exc_info=True)
    except Exception:  # noqa: BLE001
        logger.warning("running-session reconcile failed", exc_info=True)

    if imported or skipped:
        logger.info(
            "live-turn reconciliation: imported=%d skipped=%d", imported, skipped
        )
    return {"imported": imported, "skipped": skipped}
