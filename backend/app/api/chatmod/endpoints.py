"""拆分模块 `endpoints`（含 MAX_CONCURRENT_AGENTS、MAX_QUEUE_SIZE、_agent_semaphore、_get_agent_semaphore、_queue_counter、chat_multi_agent、chat_multi_agent_stream、router）。

原文件 docstring: 聊天 API 路由模块。

提供聊天对话的创建、流式响应、历史记录管理等功能。"""

# ── 复制自原模块的顶层 import ──

import asyncio

import json

import logging

import uuid

from fastapi import APIRouter, HTTPException, Request

from fastapi.responses import StreamingResponse

from app.config import settings

from app.context.token_counter import estimate_tokens

from app.context.budget import usable_context_tokens

from app.middleware.summarization import HierarchicalSummarizationMiddleware

from app.models.schemas import ChatRequest, Source, StepEvent, MultiAgentChatResponse

from app.session import repository as session_repo

from app.session import task_bridge

from app.session.agent_executor import classify_error, PartBridgeQueue

from app.session.deps import discover_project_root

from app.agent.base import AgentMessage

from app.agent.bus import AgentBus

from app.agent.stream_events import AgentEventCollector

# ── 跨子模块依赖（自动生成）──

from .helpers import _get_user_id
from .helpers import _validate_chat_message
from .persist import _begin_task_session
from .persist import _build_compressed_history
from .persist import _persist_interrupted_partial
from .persist import _persist_multi_agent
from .persist import _resolve_multi_agent_parent

logger = logging.getLogger(__name__)

# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──

"""聊天 API 路由模块。

提供聊天对话的创建、流式响应、历史记录管理等功能。
"""

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import settings
from app.context.token_counter import estimate_tokens
from app.context.budget import usable_context_tokens

from app.middleware.summarization import HierarchicalSummarizationMiddleware
from app.models.schemas import ChatRequest, Source, StepEvent, MultiAgentChatResponse

# ── Session 管理（session.db）──
from app.session import repository as session_repo
from app.session import task_bridge
from app.session.agent_executor import classify_error, PartBridgeQueue
from app.session.deps import discover_project_root

# ── 多 Agent 系统 ──
from app.agent.base import AgentMessage
from app.agent.bus import AgentBus
from app.agent.stream_events import AgentEventCollector

logger = logging.getLogger(__name__)


router = APIRouter()


# --- 并发控制：限制同时运行的 Agent 任务数（可经 .env 的 MAX_CONCURRENT_AGENTS 调整）---

MAX_CONCURRENT_AGENTS = settings.max_concurrent_agents

_agent_semaphore: asyncio.Semaphore | None = None

_queue_counter = 0  # 正在等待 slot 的请求数

MAX_QUEUE_SIZE = 50  # B7: 排队上限，超限直接 429

def _get_agent_semaphore() -> asyncio.Semaphore:
    global _agent_semaphore
    if _agent_semaphore is None:
        _agent_semaphore = asyncio.Semaphore(MAX_CONCURRENT_AGENTS)
    return _agent_semaphore




# ═══════════════════════════════════════════════════════════════
#  多 Agent 聊天端点
# ═══════════════════════════════════════════════════════════════


@router.post("/multi-agent", response_model=MultiAgentChatResponse)
async def chat_multi_agent(request: Request, body: ChatRequest):
    """使用多 Agent 系统处理聊天请求。

    请求通过 Supervisor Agent 路由到最合适的子 Agent（如 RAG Agent）。
    支持与单 Agent 相同的参数和文件上传。
    P4：本次请求登记为 kind='task' 子会话（parent_id=主会话），支持级联取消。
    """
    _validate_chat_message(body)
    agent_bus: AgentBus = request.app.state.agent_bus
    user_id = _get_user_id(request)
    service, session_id, session_dir = _resolve_multi_agent_parent(request, user_id, body.conversation_id, body.directory)

    compressed = await _build_compressed_history(service, user_id, session_id)

    # 登记子任务会话（kind='task'）+ AgentBus thread
    child_id, thread_id = _begin_task_session(service, user_id, session_id, body.message)

    try:
        # 通过 Supervisor 发送请求
        reply = await agent_bus.send_and_wait(
            AgentMessage(
                source="user",
                target="supervisor",
                type="request",
                action="chat",
                payload={
                    "question": body.message,
                    "model": body.model,
                    "history": compressed,
                    "use_vector_db": body.use_vector_db,
                    "files": [f.model_dump() for f in body.files],
                    "conversation_id": session_id,
                    "user_id": user_id,
                    "directory": session_dir,
                },
                thread_id=thread_id,
            ),
            timeout=settings.supervisor_timeout,
        )
    except asyncio.CancelledError:
        task_bridge.unregister(child_id)
        service.update(user_id, child_id, status="interrupted")
        raise HTTPException(status_code=499, detail="Request cancelled")
    except Exception as e:
        task_bridge.unregister(child_id)
        service.update(user_id, child_id, status="error")
        logger.exception("multi-agent request failed: user=%s session=%s classified=%s",
                         user_id, session_id, classify_error(e))
        raise HTTPException(status_code=500, detail="处理请求时发生内部错误，请稍后重试")

    if reply.type == "error":
        task_bridge.unregister(child_id)
        service.update(user_id, child_id, status="error")
        logger.error("multi-agent reply error: user=%s session=%s detail=%s",
                     user_id, session_id, reply.payload.get("error", ""))
        raise HTTPException(status_code=500, detail="处理请求时发生内部错误，请稍后重试")

    payload = reply.payload
    answer = payload.get("answer", "")
    sources = payload.get("sources", [])
    steps = payload.get("steps", [])
    routed_to = payload.get("routed_to")

    # 落库：主会话 + 子任务会话
    user_msg_id, assistant_msg_id = await _persist_multi_agent(
        service, user_id, session_id, child_id, body.message, answer, sources, steps,
        model=body.model, tokens=payload.get("tokens"), client_msg_id=body.client_msg_id,
        files=[f.model_dump() for f in body.files],
        voice=body.voice.model_dump() if body.voice else None,
    )
    task_bridge.unregister(child_id)

    return MultiAgentChatResponse(
        answer=answer,
        sources=[Source(**s) if isinstance(s, dict) else s for s in sources],
        conversation_id=session_id,
        steps=[StepEvent(**s) if isinstance(s, dict) else s for s in steps],
        routed_to=routed_to,
    )



# ═══════════════════════════════════════════════════════════════
#  多 Agent 流式聊天端点
# ═══════════════════════════════════════════════════════════════


@router.post("/multi-agent/stream")
async def chat_multi_agent_stream(request: Request, body: ChatRequest):
    """使用多 Agent 系统的流式聊天端点。

    工作流程:
      1. 通过 Supervisor 将请求路由到最合适的子 Agent
      2. 流式返回各步骤事件（路由、检索、生成等）
      3. 最终返回完整响应

    SSE 事件类型:
      - "routing":      正在路由到某个 Agent
      - "step_start":   步骤开始（当子 Agent 支持时）
      - "step_end":     步骤完成
      - "done":         所有处理完成，包含最终回答
      - "error":        处理出错

    P4：请求登记为 kind='task' 子会话，删除/打断父会话时级联取消。
    """
    _validate_chat_message(body)
    agent_bus: AgentBus = request.app.state.agent_bus
    user_id = _get_user_id(request)
    service, session_id, session_dir = _resolve_multi_agent_parent(request, user_id, body.conversation_id, body.directory)

    compressed = await _build_compressed_history(service, user_id, session_id)

    event_queue: asyncio.Queue = asyncio.Queue()
    sem = _get_agent_semaphore()
    # 请求级事件收集器：子 Agent 的实时事件经此转发到 SSE + 记录副本（落库）
    collector = AgentEventCollector(event_queue)

    # 登记子任务会话（kind='task'）+ AgentBus thread
    child_id, thread_id = _begin_task_session(service, user_id, session_id, body.message)

    async def run_multi_agent():
        """通过 Supervisor 运行多 Agent 系统，结果推送到 event_queue。"""
        global _queue_counter
        # 仅当真正排队（进入前信号量已满）时才递增；进入后对称递减。
        # 避免直接进入（未排队）的请求也递减，导致计数失真/提前清零。
        queued_position: int | None = None
        try:
            if sem.locked():
                # B7: 排队上限检查
                if _queue_counter >= MAX_QUEUE_SIZE:
                    await event_queue.put({
                        "type": "error",
                        "error": "服务繁忙，排队已满，请稍后重试",
                        "detail": "服务繁忙，排队已满，请稍后重试",
                        "retryable": True,
                        "status_code": 429,
                        "error_type": "QueueFullError",
                    })
                    return
                _queue_counter += 1
                queued_position = _queue_counter
                await event_queue.put({
                    "type": "queued",
                    "queue_position": queued_position,
                })

            async with sem:
                if queued_position is not None:
                    _queue_counter = max(0, _queue_counter - 1)
                try:
                    # 先推送路由事件
                    await event_queue.put({
                        "type": "routing",
                        "detail": "正在分析问题并选择最合适的 Agent...",
                    })

                    # 通过 Supervisor 发送请求（_event_queue 经 payload 透传到子 Agent）
                    reply = await agent_bus.send_and_wait(
                        AgentMessage(
                            source="user",
                            target="supervisor",
                            type="request",
                            action="chat",
                            payload={
                                "question": body.message,
                                "model": body.model,
                                "history": compressed,
                                "use_vector_db": body.use_vector_db,
                                "files": [f.model_dump() for f in body.files],
                                "conversation_id": session_id,
                                "user_id": user_id,
                                "directory": session_dir,
                                "_event_queue": collector,
                            },
                            thread_id=thread_id,
                        ),
                        timeout=settings.supervisor_timeout,
                    )

                    if reply.type == "error":
                        logger.error("multi-agent reply error: session=%s detail=%s",
                                     session_id, reply.payload.get("error", ""))
                        generic_error = "处理请求时发生内部错误，请稍后重试"
                        collector.fail_running(generic_error)
                        await event_queue.put({
                            "type": "error",
                            "error": generic_error,
                            "detail": generic_error,
                            "retryable": False,
                            "status_code": None,
                            "error_type": "AgentError",
                        })
                        return

                    payload = reply.payload
                    answer = payload.get("answer", "")
                    sources = payload.get("sources", [])
                    steps = payload.get("steps", [])
                    routed_to = payload.get("routed_to")
                    agents = collector.agents_snapshot()

                    # 落库：主会话 + 子任务会话（先落库以拿到消息 id）
                    user_msg_id, assistant_msg_id = await _persist_multi_agent(
                        service, user_id, session_id, child_id, body.message, answer, sources, steps,
                        agents=agents, model=body.model, tokens=payload.get("tokens"),
                        client_msg_id=body.client_msg_id,
                        files=[f.model_dump() for f in body.files],
                        voice=body.voice.model_dump() if body.voice else None,
                    )

                    await event_queue.put({
                        "type": "done",
                        "answer": answer,
                        "sources": [
                            {"document_id": s["document_id"], "content": s["content"], "score": s["score"]}
                            if isinstance(s, dict) else s
                            for s in sources
                        ],
                        "conversation_id": session_id,
                        "user_msg_id": user_msg_id,
                        "assistant_msg_id": assistant_msg_id,
                        "steps": steps,
                        "routed_to": routed_to,
                        "agents": agents,
                        "tokens": payload.get("tokens") or {},
                    })

                except asyncio.TimeoutError:
                    collector.fail_running("请求超时，请重试")
                    await event_queue.put({
                        "type": "error",
                        "error": "请求超时，请重试",
                        "detail": "请求超时，请重试",
                        "retryable": True,
                        "status_code": None,
                        "error_type": "TimeoutError",
                    })
                except asyncio.CancelledError:
                    service.update(user_id, child_id, status="interrupted")
                    collector.fail_running("请求已取消")
                    await event_queue.put({
                        "type": "error",
                        "detail": "cancelled",
                        "retryable": False,
                        "status_code": None,
                        "error_type": "CancelledError",
                    })
                except Exception as e:
                    logger.exception("multi-agent stream invocation failed: user=%s session=%s",
                                     user_id, session_id)
                    service.update(user_id, child_id, status="error")
                    generic_error = "处理请求时发生内部错误，请稍后重试"
                    collector.fail_running(generic_error)
                    await event_queue.put({
                        "type": "error",
                        "error": generic_error,
                        "detail": generic_error,
                        **classify_error(e),
                    })
                finally:
                    task_bridge.unregister(child_id)
        except asyncio.CancelledError:
            # 排队/获取信号量期间被取消：CancelledError 在 sem.acquire() 挂起点
            # 抛出，不经过内部取消分支（try 在其之后）。在此统一清理，避免
            # 残留 zombie 子会话 / task_bridge 映射，并归还排队计数。
            if queued_position is not None:
                _queue_counter = max(0, _queue_counter - 1)
            try:
                service.update(user_id, child_id, status="interrupted")
            except Exception:
                logger.warning("failed to mark child session interrupted on queue-cancel: %s", child_id)
            task_bridge.unregister(child_id)
            raise

    async def event_generator():
        """生成 SSE 事件流（含 keep-alive 心跳）。"""
        task = asyncio.create_task(run_multi_agent())
        # B5: SSE keep-alive 心跳 — 每 20s 推一行注释，防止 Nginx/网关 60s 超时掐断
        async def _heartbeat():
            try:
                while True:
                    await asyncio.sleep(20)
                    # SSE 注释行（: 开头）不触发前端 onEvent，但维持连接活性
                    # 注意：StreamingResponse 的 yield 不能并发，心跳通过 event_queue 中转
                    await event_queue.put({"type": "_ping"})
            except asyncio.CancelledError:
                pass
        heartbeat = asyncio.create_task(_heartbeat())
        # [B11] 是否已正常走到 done/error（此时 _persist_multi_agent 已在 run_multi_agent 完成落库）
        reached_terminal: dict | None = None
        try:
            while True:
                event = await event_queue.get()
                # 跳过内部心跳事件（不推给前端）
                if event.get("type") == "_ping":
                    continue
                # 注入 conversation_id：前端在流中尽早拿到会话 id，
                # 使"停止"按钮能调用 /api/sessions/{id}/interrupt 真正打断后台任务
                event["conversation_id"] = session_id
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in ("done", "error"):
                    reached_terminal = event
                    break
            await task
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            # [B11] 客户端断开/取消，未走到 done/error → 兜底落库部分结果。
            # 用后台 task 而非 await：finally 可能在 GeneratorExit 上下文中执行，
            # await 会抛 "async generator ignored GeneratorExit" 破坏流关闭。
            if reached_terminal is None:
                agents = collector.agents_snapshot()
                partial_answer = "\n\n".join(
                    a.get("content", "") for a in agents if a.get("content")
                )
                if partial_answer or agents:
                    try:
                        asyncio.get_running_loop().create_task(
                            _persist_interrupted_partial(
                                service, user_id, session_id, child_id, body.message,
                                partial_answer, agents, body.client_msg_id,
                            )
                        )
                    except Exception:
                        logger.exception("failed to schedule interrupted-partial persist")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        # 响应头尽早透出会话 id：前端在读取任何 SSE 事件前即可记录 conversation_id，
        # 使"停止/撤销"按钮在任何时刻都能 POST /interrupt 真正打断后台 Agent 任务
        headers={"X-Session-Id": session_id},
    )



__all__ = ["MAX_CONCURRENT_AGENTS", "MAX_QUEUE_SIZE", "_agent_semaphore", "_get_agent_semaphore", "_queue_counter", "chat_multi_agent", "chat_multi_agent_stream", "router"]
