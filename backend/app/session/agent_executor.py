"""Agent 执行体：把 SessionCoordinator 的一次 drain 接到 RAGAgent。

对应设计文档 §7：promote 输入 → 构建上下文（history.load + epoch）→
调用 agent.invoke（SSE 事件桥接到请求级队列）→ 落库 user/assistant 消息。

executor 由 SessionService 构造时注入，逐 session 串行（coordinator 保证），
跨 session 由 coordinator 的全局 Semaphore 限流（替代旧 chat.py 的 _agent_semaphore）。
"""

import asyncio
import logging
import time as tmod
from typing import Any, Optional

from . import history as session_history
from . import repository

logger = logging.getLogger(__name__)

# request_id -> asyncio.Queue：请求级 SSE 事件桥（不入库，请求结束即清理）
_pending_queues: dict[str, asyncio.Queue] = {}


def register_request_queue(request_id: str, queue: asyncio.Queue) -> None:
    _pending_queues[request_id] = queue


def unregister_request_queue(request_id: str) -> None:
    _pending_queues.pop(request_id, None)


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


def _message_to_history(message, parts=None) -> dict[str, Any]:
    """把 session_messages 行转成模型历史消息（对齐 chat.py 的消息结构）。

    优先用 parts 里的 text part 拼内容（对齐设计 §3：正文在 Part），
    无 parts 时回退 data.content（旧数据/compaction/system 消息）。
    """
    data = message.data or {}
    role = data.get("role") or {
        "user": "user", "assistant": "assistant", "tool": "tool",
        "compaction": "system", "epoch": "system", "system": "system",
    }.get(message.type, "system")
    content = session_history.text_from_parts(parts or [], include_reasoning=True) or data.get("content", "")
    return {"id": message.id, "role": role, "content": content}


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


def _checkpoint_of(compressed: list[dict]) -> str:
    """从压缩后的历史中提取 checkpoint（摘要/截断标记）文案，未压缩返回空串。"""
    for m in compressed:
        if m.get("role") == "system":
            c = m.get("content", "")
            if c.startswith("[Conversation summary]") or c.startswith("[earlier history truncated]"):
                return c
    return ""


def build_executor(app):
    """构建 SessionService 的 executor 闭包。

    在 FastAPI 事件循环中运行；agent 从 app.state 惰性读取（ensure_runtime_state 之后才就绪）。
    """

    async def executor(session_id: str) -> None:
        item = repository.promote_next(session_id)
        if not item:
            return
        prompt: dict[str, Any] = item["prompt"] or {}
        request_id = prompt.get("request_id")
        queue = _pending_queues.get(request_id)
        first_message = repository.latest_seq(session_id) == 0

        # 1. 构建模型视角历史（epoch 之后的会话消息，不含当前轮）
        load = session_history.load(session_id)
        raw_history = [_message_to_history(m, load.parts.get(m.id)) for m in load.messages]

        # 2. 压缩/清洗（复用 chat.py 的截断 + 摘要逻辑）
        from app.api.chat import MAX_HISTORY_TOKENS, _get_summarizer, _sanitize_history, _truncate_history

        summarizer = _get_summarizer()
        if summarizer:
            compressed = await summarizer.apply(raw_history)
        else:
            compressed = _truncate_history(raw_history)
        compressed = _sanitize_history(compressed)

        # 3-6 持每会话写锁（与 multi-agent 直写 / compact / revert / fork 共享），
        #    保证同一会话的消息追加与撤销不会交错。
        lock = app.state.session_service.write_lock(session_id)
        async with lock:
            # 3. 压缩发生 → 持久化压缩基线（compaction 消息 + epoch replace + time_compacted），
            #    使恢复/重放时能定位截断水位（对齐设计 §6.3）
            checkpoint = _checkpoint_of(compressed)
            if checkpoint or (compressed and len(compressed) != len(raw_history)):
                repository.append_message(session_id, "compaction", {
                    "content": checkpoint or "[compacted]",
                    "mode": "summarize" if checkpoint else "truncate",
                })
                session_history.replace_epoch_after_compaction(
                    session_id, checkpoint or "[compacted]", {},
                )
                repository.update_session(
                    session_id, time_compacted=int(tmod.time() * 1000),
                )

            # 4. 先落库 user 消息（中断/失败时也已保留）
            user_msg = repository.append_message(session_id, "user", {
                "role": "user", "content": prompt.get("text", ""),
            })
            # 新会话首条消息 → 生成标题（在压缩消息落库后仍需判断"是否首条"）
            if first_message:
                from app.api.chat import _generate_title
                repository.update_session(
                    session_id, title=_generate_title([{"role": "user", "content": prompt.get("text", "")}])
                )

            try:
                # 5. 先建 assistant 骨架消息（parts 落库需要其 id；对齐设计 §1：正文在 Part）
                assistant_msg = repository.append_message(session_id, "assistant", {
                    "role": "assistant",
                    "parent_id": user_msg.id,
                    "agent": "rag",
                    "model": prompt.get("model"),
                    "finish": "running",
                    "tokens": {},
                })

                # 事件桥：graph 步骤/工具事件实时落库为 parts + 转发 SSE（带 part_id）
                bridge = PartBridgeQueue(queue, session_id, assistant_msg.id)

                # 6. 调用 Agent（事件桥接到请求队列）
                agent = app.state.agent
                result = await agent.invoke(
                    prompt.get("text", ""),
                    model=prompt.get("model"),
                    history=compressed,
                    use_vector_db=prompt.get("use_vector_db", True),
                    files=prompt.get("files") or [],
                    event_queue=bridge,
                    conversation_id=session_id,
                )

                # 7. 回填 assistant 结算字段 + 最终答案（text part 承载正文）
                answer = result.get("answer", "")
                final_data = dict(assistant_msg.data)
                final_data.update({
                    "content": answer,
                    "finish": result.get("finish", "stop"),
                    "model": result.get("model"),
                    "tokens": result.get("tokens") or {},
                    "steps": result.get("steps", []),
                    "sources": result.get("sources", []),
                })
                repository.update_message(session_id, assistant_msg.id, final_data)
                bridge.append_text(answer)

                # 会话级 token/费用累加
                tokens = result.get("tokens") or {}
                repository.add_session_usage(
                    session_id,
                    input_tokens=tokens.get("input", 0),
                    output_tokens=tokens.get("output", 0),
                    cost=result.get("cost") or 0.0,
                )

                if queue is not None:
                    queue.put_nowait({
                        "type": "done",
                        "answer": answer,
                        "sources": result.get("sources", []),
                        "conversation_id": session_id,
                        "steps": result.get("steps", []),
                        "parts": [p.model_dump() for p in repository.list_parts(assistant_msg.id)],
                        "user_msg_id": user_msg.id,
                        "assistant_msg_id": assistant_msg.id,
                    })
            except asyncio.CancelledError:
                # 中断：assistant 骨架标记为错误，避免空消息残留
                try:
                    repository.update_message(session_id, assistant_msg.id, {
                        **dict(assistant_msg.data),
                        "content": "请求已中断", "finish": "error",
                    })
                except Exception:  # noqa: BLE001
                    pass
                if queue is not None:
                    queue.put_nowait({"type": "error", "detail": "cancelled",
                                      "retryable": False, "status_code": None, "error_type": "CancelledError"})
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("executor: session %s agent call failed", session_id, exc_info=exc)
                try:
                    repository.update_message(session_id, assistant_msg.id, {
                        **dict(assistant_msg.data),
                        "content": str(exc) or "Agent 调用失败", "finish": "error",
                    })
                except Exception:  # noqa: BLE001
                    pass
                if queue is not None:
                    queue.put_nowait({"type": "error", "detail": str(exc), **classify_error(exc)})

    return executor
