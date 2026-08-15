"""Agent 执行体辅助（多 Agent 共用）。

单 Agent executor（SessionCoordinator + RAGAgent 直连）已随单 Agent 模式移除，
本模块保留多 Agent 链路共用的两件套：
- classify_error：统一错误分类（retryable/status_code/error_type）
- PartBridgeQueue：graph 事件 → message_parts 落库 + SSE 转发
"""

import logging
import time as tmod
from typing import Any, Optional

from . import repository

logger = logging.getLogger(__name__)


def classify_error(exc: Exception) -> dict[str, Any]:
    """错误分类（对齐 chat.py run_agent 的 retryable/status_code 判定）。"""
    error_str = str(exc).lower()
    error_type = type(exc).__name__
    retryable = False
    status_code = None

    if "ratelimit" in error_type or "429" in error_str or "rate limit" in error_str or "too many requests" in error_str:
        retryable, status_code = True, 429
    elif any(x in error_str for x in ("500", "502", "503", "504")):
        retryable, status_code = True, 500
    elif "internalserverserror" in error_type or "internal server error" in error_str:
        retryable, status_code = True, 500
    elif "timeout" in error_str or "timed out" in error_str:
        retryable = True
    elif "connection" in error_str and ("error" in error_str or "refused" in error_str or "reset" in error_str):
        retryable = True
    elif "overloaded" in error_str or "service_unavailable" in error_str:
        retryable, status_code = True, 503
    return {"retryable": retryable, "status_code": status_code, "error_type": error_type}


class PartBridgeQueue:
    """把 graph 事件实时落库为 message_parts，同时转发给请求级 SSE 队列。

    事件 → part 映射（对齐设计 §2 Part 类型族）：
      step_start  → step-start part（state: running）
      step_end    → step-finish part
      tool_start  → tool part（state: running）
      tool_end    → 更新同一 tool part（state: completed + output）
      tool_output / tool_heartbeat / step → 噪音，仅转发不落库

    转发的 SSE 事件带上 part_id，前端可按 part 定位/增量渲染。
    """

    def __init__(self, inner_queue, session_id: str, message_id: str):
        self._inner = inner_queue
        self._session_id = session_id
        self._message_id = message_id
        self._tool_parts: dict[str, str] = {}
        self._tool_meta: dict[str, dict] = {}
        self._text_part_id: str | None = None
        self._text_buffer = ""

    def put_nowait(self, event: dict) -> None:
        try:
            event = self._persist(event)
        except Exception:  # noqa: BLE001
            logger.exception("persist part failed: %s", event.get("type"))
        if self._inner is not None:
            try:
                self._inner.put_nowait(event)
            except Exception:  # noqa: BLE001
                pass

    def append_text(self, text: str) -> None:
        """把最终答案追加/覆盖为 text part（在 invoke 结束后调用一次）。

        若期间已有流式 text_delta 建立的 text part，则用权威最终答案覆盖
        （含截断提示、强制收尾等兜底内容），避免留下不完整增量。
        """
        if not text:
            return
        if self._text_part_id is not None:
            repository.update_part(self._session_id, self._text_part_id, {"text": text})
        else:
            repository.append_part(self._session_id, self._message_id, "text", {"text": text})

    def replay_agent_steps(self, steps: list, agent_id: str = "") -> None:
        """回放子代理的步骤/工具事件为 parts（多代理子会话按 agent_id 归档）。"""
        for s in steps or []:
            if not isinstance(s, dict):
                continue
            if s.get("type") in ("step_start", "step_end", "tool_start", "tool_end"):
                try:
                    self._persist({**s, "agent_id": agent_id})
                except Exception:  # noqa: BLE001
                    logger.exception("replay step persist failed")

    def append_agent(self, info: dict) -> None:
        """把子代理执行信息落库为 agent part（多代理消息归属展示用）。"""
        try:
            repository.append_part(self._session_id, self._message_id, "agent", {
                "agent_id": info.get("agent_id", ""),
                "agent_name": info.get("agent_name", info.get("agent_id", "")),
                "status": info.get("status", "completed"),
            })
        except Exception:  # noqa: BLE001
            logger.exception("append agent part failed")

    def _tool_key(self, event: dict) -> str:
        agent = event.get("agent_id")
        return f"{agent}:{event.get('step_id', '')}" if agent else f":{event.get('step_id', '')}"

    def _persist(self, event: dict) -> dict:
        """落库 part 并给事件附加 part_id，返回（可能被修改的）事件副本。"""
        out = dict(event)
        et = event.get("type")
        now = int(tmod.time() * 1000)
        if et == "step_start":
            part = repository.append_part(self._session_id, self._message_id, "step-start", {
                "step_id": event.get("step_id", ""),
                "state": "running",
                "name": event.get("name", ""),
            })
            out["part_id"] = part.id
        elif et == "step_end":
            part = repository.append_part(self._session_id, self._message_id, "step-finish", {
                "step_id": event.get("step_id", ""),
                "state": event.get("status", "completed"),
                "name": event.get("name", ""),
                "detail": event.get("detail", ""),
                "duration_ms": event.get("duration_ms", 0),
            })
            out["part_id"] = part.id
        elif et == "tool_start":
            key = self._tool_key(event)
            self._tool_meta[key] = {
                "args": event.get("tool_args") or {},
                "time_start": now,
            }
            part = repository.append_part(self._session_id, self._message_id, "tool", {
                "agent_id": event.get("agent_id", ""),
                "step_id": event.get("step_id", ""),
                "state": "running",
                "name": event.get("tool_name", ""),
                "args": event.get("tool_args") or {},
                "output": "",
                "time_start": now,
            })
            self._tool_parts[key] = part.id
            out["part_id"] = part.id
        elif et == "tool_end":
            key = self._tool_key(event)
            pid = self._tool_parts.get(key)
            if pid:
                meta = self._tool_meta.get(key) or {}
                updated = repository.update_part(self._session_id, pid, {
                    "agent_id": event.get("agent_id", ""),
                    "step_id": event.get("step_id", ""),
                    "state": event.get("status", "completed"),
                    "name": event.get("tool_name", ""),
                    "args": meta.get("args") or event.get("tool_args") or {},
                    "output": event.get("tool_result", "") or "",
                    "time_start": meta.get("time_start"),
                    "time_end": now,
                })
                if updated:
                    out["part_id"] = updated.id
        elif et == "text_delta":
            # 流式文本增量：累积进 text part，实时 update（不产生新 part）
            self._text_buffer += event.get("delta", "") or ""
            if self._text_part_id is None:
                part = repository.append_part(self._session_id, self._message_id, "text", {
                    "text": self._text_buffer,
                })
                self._text_part_id = part.id
            else:
                repository.update_part(self._session_id, self._text_part_id, {"text": self._text_buffer})
            out["part_id"] = self._text_part_id
            out["delta"] = event.get("delta", "")
        return out
