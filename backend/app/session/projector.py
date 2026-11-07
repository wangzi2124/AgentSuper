"""Session conversation projector.

session.db 是对话正文的真相源；checkpoint 只做单轮运行时并双写对账。
本模块负责：

- 把图 checkpoint 中的消息幂等投影到 ``session_messages`` / ``message_parts``；
- 从 ``session_messages`` 重建模型视角（含原生 assistant tool_calls + tool 结果）；
- 应用最新压缩水位（摘要 + ``tail_start_id``），不物理删除历史。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from . import repository

logger = logging.getLogger(__name__)

_ROLE_TO_TYPE = {
    "human": "user",
    "user": "user",
    "ai": "assistant",
    "assistant": "assistant",
    "tool": "tool",
    "system": "system",
}


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        return "\n".join(parts)
    return "" if content is None else str(content)


def _additional_kwargs(msg: Any) -> dict[str, Any]:
    value = (
        msg.get("additional_kwargs")
        if isinstance(msg, dict)
        else getattr(msg, "additional_kwargs", None)
    )
    return value if isinstance(value, dict) else {}


def _role(msg: Any) -> str:
    value = msg.get("role") or msg.get("type") if isinstance(msg, dict) else getattr(msg, "type", "")
    return _ROLE_TO_TYPE.get(str(value or ""), "system")


def stable_message_id(session_id: str, msg: Any) -> str:
    mid = msg.get("id") if isinstance(msg, dict) else getattr(msg, "id", None)
    if mid:
        return str(mid)
    content = _content_text(
        msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
    )
    extra = {
        "tool_calls": _tool_calls(msg),
        "tool_call_id": msg.get("tool_call_id") if isinstance(msg, dict) else getattr(msg, "tool_call_id", None),
        "name": msg.get("name") if isinstance(msg, dict) else getattr(msg, "name", None),
    }
    raw = json.dumps(
        {"content": content, **extra}, ensure_ascii=False, sort_keys=True, default=str
    )
    digest = hashlib.sha256(f"{_role(msg)}|{raw}".encode("utf-8")).hexdigest()[:16]
    return f"msg_{digest}"


def _tool_calls(msg: Any) -> list[dict[str, Any]]:
    value = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)
    return [dict(c) for c in value if isinstance(c, dict)] if isinstance(value, list) else []


def _message_data(msg: Any) -> dict[str, Any]:
    content = _content_text(
        msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
    )
    ak = _additional_kwargs(msg)
    data: dict[str, Any] = {"content": content}
    files = ak.get("files")
    if isinstance(files, list) and files:
        data["files"] = [f for f in files if isinstance(f, dict)]
    ui = ak.get("ui")
    if isinstance(ui, dict) and ui:
        data["ui"] = ui
    calls = _tool_calls(msg)
    if calls:
        data["tool_calls"] = calls
    tool_call_id = (
        msg.get("tool_call_id")
        if isinstance(msg, dict)
        else getattr(msg, "tool_call_id", None)
    )
    if tool_call_id:
        data["tool_call_id"] = str(tool_call_id)
    name = msg.get("name") if isinstance(msg, dict) else getattr(msg, "name", None)
    if name:
        data["tool_name"] = str(name)
    return data


def project_user_message(
    session_id: str,
    message_id: str,
    text: str,
    files: Optional[list[Any]] = None,
    voice: Optional[dict[str, Any]] = None,
) -> None:
    """在回合开始即落库当前 user 消息（进程中断也不丢用户输入）。"""
    try:
        repository.require_session(session_id)
    except Exception:  # noqa: BLE001 - 测试/临时 ctx 未落库
        return
    data: dict[str, Any] = {"content": text}
    if files:
        data["files"] = [f for f in files if isinstance(f, dict)]
    if voice:
        data["voice"] = voice
    repository.upsert_message(session_id, message_id, "user", data)


def project_checkpoint_messages(
    session_id: str, messages: list[Any], *, skip_ids: Optional[set[str]] = None
) -> int:
    """把 checkpoint 消息幂等写入 session.db，返回新写入/更新的条数。

    ``skip_ids`` 是逻辑删除/撤销的墓碑集合，避免 checkpoint 重新投影复活。
    """
    try:
        repository.require_session(session_id)
    except Exception:  # noqa: BLE001 - 测试/临时 ctx 未落库
        return 0
    skip = skip_ids or set()
    written = 0
    # 同一内容的多条无 id 消息（例如空内容工具轮）用出现序号区分，避免碰撞。
    seen: dict[str, int] = {}
    # assistant tool_call_id -> (message_id, tool name)，用于给 tool 结果挂 part。
    parent_by_call: dict[str, tuple[str, str]] = {}
    for msg in messages:
        msg_type = _role(msg)
        if msg_type not in ("user", "assistant", "tool", "system"):
            continue
        base = stable_message_id(session_id, msg)
        count = seen.get(base, 0) + 1
        seen[base] = count
        mid = base if count == 1 else f"{base}__{count}"
        if mid in skip:
            continue
        data = _message_data(msg)
        repository.upsert_message(session_id, mid, msg_type, data)
        written += 1

        if msg_type == "assistant":
            for call in data.get("tool_calls") or []:
                call_id = str(call.get("id") or "")
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                name = str(fn.get("name") or call.get("name") or "")
                raw_args = fn.get("arguments")
                if raw_args is None and "args" in call:
                    raw_args = call.get("args")
                args = raw_args if isinstance(raw_args, dict) else {}
                if isinstance(raw_args, str):
                    try:
                        parsed = json.loads(raw_args)
                        args = parsed if isinstance(parsed, dict) else {}
                    except Exception:  # noqa: BLE001
                        args = {}
                if call_id:
                    parent_by_call[call_id] = (mid, name)
                    if repository.find_tool_part(session_id, mid, call_id) is None:
                        repository.append_part(
                            session_id,
                            mid,
                            "tool",
                            {
                                "state": "running",
                                "name": name,
                                "args": args,
                                "arguments": raw_args if isinstance(raw_args, str) else json.dumps(args, ensure_ascii=False),
                                "tool_call_id": call_id,
                                "output": "",
                            },
                        )
        elif msg_type == "tool":
            call_id = str(data.get("tool_call_id") or "")
            parent = parent_by_call.get(call_id)
            if parent:
                part = repository.find_tool_part(session_id, parent[0], call_id)
                if part is not None:
                    payload = dict(part.data)
                    payload["state"] = "completed"
                    payload["output"] = data.get("content") or ""
                    payload["name"] = payload.get("name") or parent[1]
                    repository.update_part(session_id, part.id, payload)
    return written


def to_lc_message(msg: repository.Message) -> Any:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    data = msg.data or {}
    content = data.get("content") or ""
    if msg.type == "user":
        kwargs: dict[str, Any] = {}
        if data.get("files"):
            kwargs["files"] = data["files"]
        return HumanMessage(content=content, id=msg.id, additional_kwargs=kwargs)
    if msg.type == "assistant":
        kwargs = {}
        if data.get("ui"):
            kwargs["ui"] = data["ui"]
        return AIMessage(
            content=content,
            id=msg.id,
            tool_calls=data.get("tool_calls") or [],
            additional_kwargs=kwargs,
        )
    if msg.type == "tool":
        prune = data.get("prune") if isinstance(data.get("prune"), dict) else None
        if prune:
            from cognition.tool_output import prune_stub

            content = prune_stub(
                str(data.get("tool_name") or ""),
                int(prune.get("tokens") or 0),
                str(prune.get("path") or ""),
            )
        return ToolMessage(
            content=content,
            tool_call_id=str(data.get("tool_call_id") or ""),
            id=msg.id,
            name=str(data.get("tool_name") or ""),
        )
    return SystemMessage(content=content, id=msg.id)


def rebuild_model_messages(
    session_id: str,
    *,
    hidden_ids: Optional[set[str]] = None,
    baseline: Optional[int] = None,
) -> list[Any]:
    """从 session.db 重建历史模型视图（不含本轮当前 user）。

    ``baseline`` 是压缩水位：撤销到压缩之前时基线回退，晚于基线的压缩行
    不再生效；``None`` 表示调用方不限制（测试/离线工具）。
    """
    from .compaction import CompactionCheckpoint

    db = CompactionCheckpoint.db_view(
        session_id, hidden_ids=hidden_ids, baseline=baseline
    )
    view: list[Any] = []
    checkpoint = db.checkpoint
    if checkpoint is not None and checkpoint.summary:
        from langchain_core.messages import SystemMessage

        view.append(
            SystemMessage(
                content=f"[历史摘要]\n{checkpoint.summary}",
                id=f"{session_id}_summary_{checkpoint.seq}",
                additional_kwargs={"compaction": True},
            )
        )
    view.extend(to_lc_message(m) for m in db.messages)
    return view


def conversation_ids(session_id: str) -> list[str]:
    return [m.id for m in repository.list_conversation_messages(session_id)]


__all__ = [
    "conversation_ids",
    "project_checkpoint_messages",
    "project_user_message",
    "rebuild_model_messages",
    "stable_message_id",
    "to_lc_message",
]
