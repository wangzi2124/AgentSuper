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


def _message_to_history(message) -> dict[str, Any]:
    """把 session_messages 行转成模型历史消息（对齐 chat.py 的消息结构）。"""
    data = message.data or {}
    role = data.get("role") or {
        "user": "user", "assistant": "assistant", "tool": "tool",
        "compaction": "system", "epoch": "system", "system": "system",
    }.get(message.type, "system")
    return {"id": message.id, "role": role, "content": data.get("content", "")}


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
        raw_history = [_message_to_history(m) for m in load.messages]

        # 2. 压缩/清洗（复用 chat.py 的截断 + 摘要逻辑）
        from app.api.chat import MAX_HISTORY_TOKENS, _get_summarizer, _sanitize_history, _truncate_history

        summarizer = _get_summarizer()
        if summarizer:
            compressed = await summarizer.apply(raw_history)
        else:
            compressed = _truncate_history(raw_history)
        compressed = _sanitize_history(compressed)

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
            # 5. 调用 Agent（事件桥接到请求队列）
            agent = app.state.agent
            result = await agent.invoke(
                prompt.get("text", ""),
                model=prompt.get("model"),
                history=compressed,
                use_vector_db=prompt.get("use_vector_db", True),
                files=prompt.get("files") or [],
                event_queue=queue,
                conversation_id=session_id,
            )

            # 6. 落库 assistant 消息
            assistant_msg = repository.append_message(session_id, "assistant", {
                "role": "assistant", "content": result.get("answer", ""),
                "sources": result.get("sources", []),
                "steps": result.get("steps", []),
            })

            if queue is not None:
                queue.put_nowait({
                    "type": "done",
                    "answer": result.get("answer", ""),
                    "sources": result.get("sources", []),
                    "conversation_id": session_id,
                    "steps": result.get("steps", []),
                    "user_msg_id": user_msg.id,
                    "assistant_msg_id": assistant_msg.id,
                })
        except asyncio.CancelledError:
            if queue is not None:
                queue.put_nowait({"type": "error", "detail": "cancelled",
                                  "retryable": False, "status_code": None, "error_type": "CancelledError"})
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("executor: session %s agent call failed", session_id, exc_info=exc)
            if queue is not None:
                queue.put_nowait({"type": "error", "detail": str(exc), **classify_error(exc)})

    return executor
