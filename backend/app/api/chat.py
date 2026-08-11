"""聊天 API 路由模块。

提供聊天对话的创建、流式响应、历史记录管理等功能。
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import settings
from app.context.token_counter import estimate_tokens

from app.middleware.summarization import HierarchicalSummarizationMiddleware
from app.models.schemas import ChatRequest, ChatResponse, Source, StepEvent, MultiAgentChatResponse

# ── Session 管理（session.db）──
from app.session import repository as session_repo
from app.session import task_bridge
from app.session.agent_executor import register_request_queue, unregister_request_queue, classify_error
from app.session.agent_executor import PartBridgeQueue
from app.session.deps import discover_project_root

# ── 多 Agent 系统 ──
from app.agent.base import AgentMessage
from app.agent.bus import AgentBus
from app.agent.stream_events import AgentEventCollector

logger = logging.getLogger(__name__)

router = APIRouter()

# --- 用户身份：优先从 X-User-Id 头获取，默认 anonymous ---
_DEFAULT_USER_ID = "anonymous"

def _get_user_id(request: Request) -> str:
    """从请求头中提取用户身份。"""
    uid = request.headers.get("X-User-Id", "")
    return uid.strip() if uid.strip() else _DEFAULT_USER_ID

# --- 并发控制：限制同时运行的 Agent 任务数（可经 .env 的 MAX_CONCURRENT_AGENTS 调整）---
MAX_CONCURRENT_AGENTS = settings.max_concurrent_agents
_agent_semaphore: asyncio.Semaphore | None = None
_queue_counter = 0  # 正在等待 slot 的请求数


def _get_agent_semaphore() -> asyncio.Semaphore:
    global _agent_semaphore
    if _agent_semaphore is None:
        _agent_semaphore = asyncio.Semaphore(MAX_CONCURRENT_AGENTS)
    return _agent_semaphore


# Sliding window: keep up to 48K tokens of history before passing to Agent.
# The Agent internally truncates to 1M tokens in graph.py, so this threshold
# is just for DB storage efficiency, not for context management.
MAX_HISTORY_TOKENS = 48_000

_summarizer: HierarchicalSummarizationMiddleware | None = None
_summarizer_model: str | None = None


def _get_summarizer() -> HierarchicalSummarizationMiddleware | None:
    """获取或初始化摘要中间件单例。"""
    global _summarizer, _summarizer_model
    current_model = settings.summarization_model

    if current_model != _summarizer_model:
        _summarizer = None
        _summarizer_model = current_model

    if _summarizer is not None:
        return _summarizer
    if not current_model:
        return None
    _summarizer = HierarchicalSummarizationMiddleware(
        model=current_model,
        trigger=("tokens", MAX_HISTORY_TOKENS),
        keep=("messages", settings.summarization_keep_messages),
        api_key=settings.summarization_api_key or settings.llm_api_key,
        api_base=settings.summarization_api_base or settings.llm_api_base,
    )
    return _summarizer


def reset_summarizer():
    """重置摘要中间件状态。"""
    global _summarizer, _summarizer_model
    _summarizer = None
    _summarizer_model = None


def _generate_title(messages: list[dict]) -> str:
    """根据用户第一条消息生成对话标题。"""
    for msg in messages:
        if msg.get("role") == "user":
            text = msg.get("content", "")
            text = text.strip().replace("\n", " ")
            return text[:20] + ("..." if len(text) > 20 else "")
    return "新对话"


def _truncate_history(history: list[dict], max_tokens: int = MAX_HISTORY_TOKENS) -> list[dict]:
    """截断对话历史以控制token数量。确保至少保留最近一条消息。"""
    if not history:
        return []
    total = 0
    truncated = []
    # Always keep at least the last message
    for msg in reversed(history):
        tokens = estimate_tokens(msg.get("content", ""))
        if total + tokens > max_tokens and truncated:
            truncated.append({"role": "system", "content": "[earlier history truncated]"})
            break
        total += tokens
        truncated.append(msg)
    truncated.reverse()
    return truncated


# 发给模型时只保留必要字段，去掉 id / steps / sources 等内部字段
_ALLOWED_HISTORY_KEYS = {"role", "content", "name", "tool_call_id", "tool_calls"}


def _sanitize_history(history: list[dict]) -> list[dict]:
    """清洗历史消息：仅保留模型需要的最小字段集合。

    DB 中的消息带有 id（前端编辑用）、steps（工具调用过程，含 tool_args/tool_result）、
    sources 等字段。这些字段原样塞进 LLM 请求会白白增大请求体积（且部分网关会拒绝），
    这里统一剥离。
    """
    cleaned = []
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in ("user", "assistant", "system", "tool"):
            continue
        out = {k: v for k, v in msg.items() if k in _ALLOWED_HISTORY_KEYS}
        # 至少要有内容或工具调用，否则跳过
        if out.get("content") is None and not out.get("tool_calls"):
            continue
        cleaned.append(out)
    return cleaned


# ═══════════════════════════════════════════════════════════════
#  Session 管理接线（设计文档 P3）：/stream 走 SessionService，
#  会话 CRUD 统一读 session.db
# ═══════════════════════════════════════════════════════════════

def _get_session_service(request: Request):
    return request.app.state.session_service


def _msg_type_to_role(msg_type: str) -> str:
    return {
        "user": "user", "assistant": "assistant", "tool": "tool",
        "compaction": "system", "epoch": "system", "system": "system",
    }.get(msg_type, "system")


def _fmt_time(ms: int) -> str:
    """epoch ms -> ISO 字符串（兼容旧 conversations.created_at 格式）。"""
    return datetime.fromtimestamp(ms / 1000).isoformat() if ms else ""


# ═══════════════════════════════════════════════════════════════
#  Multi-Agent 子任务会话（设计文档 P4）：
#  每次 send_and_wait 登记为 kind='task' 子会话，级联取消经 task_bridge
# ═══════════════════════════════════════════════════════════════

def _resolve_multi_agent_parent(request: Request, user_id: str, conv_id: str | None) -> tuple[object, str]:
    """解析/创建 multi-agent 主会话（session.db）。"""
    service = _get_session_service(request)
    if conv_id:
        try:
            service.get(user_id, conv_id)
        except session_repo.SessionNotFound:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return service, conv_id
    else:
        session = service.create(user_id, directory=discover_project_root(), kind="multi-agent")
        return service, session.id


def _session_history_for(service, user_id: str, session_id: str) -> list[dict]:
    """把会话消息投影为模型历史（role/content）。

    正文优先从 message_parts 的 text part 提取（对齐设计 §3），无 parts 时
    回退 data.content（旧数据/compaction/system 消息）。
    """
    from app.session import history as session_history

    messages = service.messages(user_id, session_id)
    ids = [m.id for m in messages]
    parts_map = session_repo.list_parts_for_messages(ids) if ids else {}
    history = []
    for m in messages:
        role = m.data.get("role") or _msg_type_to_role(m.type)
        if role in ("user", "assistant", "system"):
            content = session_history.text_from_parts(parts_map.get(m.id, [])) or m.data.get("content", "")
            history.append({"role": role, "content": content})
    return history


def _begin_task_session(service, user_id: str, parent_id: str, question: str) -> tuple[str, str]:
    """创建 kind='task' 子会话并登记 thread（返回 child_id + thread_id）。"""
    thread_id = f"{parent_id}:task:{uuid.uuid4().hex[:8]}"
    title = question.strip().replace("\n", " ")[:20]
    child = service.create(user_id, parent_id=parent_id, kind="task",
                           agent="supervisor", title=title or "任务")
    task_bridge.register(child.id, thread_id)
    return child.id, thread_id


def _persist_multi_agent_parts(session_id: str, message_id: str, answer: str,
                               agents: list | None = None) -> None:
    """把多代理 assistant 消息的正文/子代理执行信息落库为 parts。

    子代理步骤先按 agent 归档（agent part + 各步骤/工具 part 带 agent_id），
    最后以 text part 承载最终答案正文。
    """
    bridge = PartBridgeQueue(None, session_id, message_id)
    try:
        for a in agents or []:
            if isinstance(a, dict):
                bridge.append_agent(a)
                bridge.replay_agent_steps(a.get("steps") or [], agent_id=a.get("agent_id", ""))
        bridge.append_text(answer)
    except Exception:  # noqa: BLE001
        logger.exception("persist multi-agent parts failed")


async def _persist_multi_agent(service, user_id: str, session_id: str, child_id: str,
                               question: str, answer: str, sources: list, steps: list,
                               agents: list | None = None, model: str | None = None) -> tuple[str, str]:
    """主会话 + 子任务会话各追加 user/assistant 消息；新会话生成标题。

    主会话写经 write_lock 串行化（与 /stream 协调器执行体、compact/revert 互斥），
    保证同一会话的消息顺序不被交错。
    """
    async with service.write_lock(session_id):
        user_msg = service.append_message(user_id, session_id, "user", {"role": "user", "content": question})
        if session_repo.latest_seq(session_id) == 1:
            service.update(user_id, session_id, title=_generate_title([{"role": "user", "content": question}]))
        assistant_msg = service.append_message(user_id, session_id, "assistant", {
            "role": "assistant", "content": answer, "sources": sources, "steps": steps,
            "agents": agents or [], "parent_id": user_msg.id, "agent": "supervisor", "model": model,
        })
        _persist_multi_agent_parts(session_id, assistant_msg.id, answer, agents)
    # 子任务会话独立日志（隔离上下文）
    async with service.write_lock(child_id):
        service.append_message(user_id, child_id, "user", {"role": "user", "content": question})
        child_assist = service.append_message(user_id, child_id, "assistant", {
            "role": "assistant", "content": answer, "sources": sources, "steps": steps,
            "agents": agents or [], "parent_id": user_msg.id, "agent": "supervisor", "model": model,
        })
        _persist_multi_agent_parts(child_id, child_assist.id, answer, agents)
        service.update(user_id, child_id, status="idle")
    return user_msg.id, assistant_msg.id



@router.post("/", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest):
    """处理非流式聊天请求，返回完整响应（消息落库 session.db）。"""
    agent = request.app.state.agent
    user_id = _get_user_id(request)
    service = _get_session_service(request)

    if body.conversation_id:
        session_id = body.conversation_id
        try:
            service.get(user_id, session_id)
        except session_repo.SessionNotFound:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        session = service.create(user_id, directory=discover_project_root(), kind="chat")
        session_id = session.id

    history = _session_history_for(service, user_id, session_id)

    summarizer = _get_summarizer()
    if summarizer:
        compressed = await summarizer.apply(history)
    else:
        compressed = _truncate_history(history)
    compressed = _sanitize_history(compressed)

    # Build multimodal user content
    user_content: list[dict] = [{"type": "text", "text": body.message}]
    for f in body.files:
        if f.mime_type.startswith("image/"):
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{f.mime_type};base64,{f.data}"},
            })

    try:
        result = await agent.invoke(body.message, model=body.model, history=compressed, use_vector_db=body.use_vector_db, files=[f.model_dump() for f in body.files])
    except Exception as e:
        logger.exception("chat invocation failed")
        raise HTTPException(status_code=500, detail="Internal server error")

    async with service.write_lock(session_id):
        service.append_message(user_id, session_id, "user", {"role": "user", "content": body.message})
        if session_repo.latest_seq(session_id) == 1:
            service.update(user_id, session_id, title=_generate_title([{"role": "user", "content": body.message}]))
        service.append_message(user_id, session_id, "assistant", {
            "role": "assistant",
            "content": result["answer"],
            "sources": result.get("sources", []),
            "steps": result.get("steps", []),
        })

    return ChatResponse(
        answer=result["answer"],
        sources=[
            Source(
                document_id=s["document_id"],
                content=s["content"],
                score=s["score"],
            )
            for s in result["sources"]
        ],
        conversation_id=session_id,
        steps=[StepEvent(**s) for s in result.get("steps", [])],
    )


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
    agent_bus: AgentBus = request.app.state.agent_bus
    user_id = _get_user_id(request)
    service, session_id = _resolve_multi_agent_parent(request, user_id, body.conversation_id)

    history = _session_history_for(service, user_id, session_id)

    summarizer = _get_summarizer()
    if summarizer:
        compressed = await summarizer.apply(history)
    else:
        compressed = _truncate_history(history)
    compressed = _sanitize_history(compressed)

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
        raise HTTPException(status_code=500, detail=f"Agent error: {e}")

    if reply.type == "error":
        task_bridge.unregister(child_id)
        service.update(user_id, child_id, status="error")
        raise HTTPException(status_code=500, detail=reply.payload.get("error", "Agent error"))

    payload = reply.payload
    answer = payload.get("answer", "")
    sources = payload.get("sources", [])
    steps = payload.get("steps", [])
    routed_to = payload.get("routed_to")

    # 落库：主会话 + 子任务会话
    user_msg_id, assistant_msg_id = await _persist_multi_agent(
        service, user_id, session_id, child_id, body.message, answer, sources, steps,
        model=body.model,
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
    agent_bus: AgentBus = request.app.state.agent_bus
    user_id = _get_user_id(request)
    service, session_id = _resolve_multi_agent_parent(request, user_id, body.conversation_id)

    history = _session_history_for(service, user_id, session_id)

    summarizer = _get_summarizer()
    if summarizer:
        compressed = await summarizer.apply(history)
    else:
        compressed = _truncate_history(history)
    compressed = _sanitize_history(compressed)

    event_queue: asyncio.Queue = asyncio.Queue()
    sem = _get_agent_semaphore()
    # 请求级事件收集器：子 Agent 的实时事件经此转发到 SSE + 记录副本（落库）
    collector = AgentEventCollector(event_queue)

    # 登记子任务会话（kind='task'）+ AgentBus thread
    child_id, thread_id = _begin_task_session(service, user_id, session_id, body.message)

    async def run_multi_agent():
        """通过 Supervisor 运行多 Agent 系统，结果推送到 event_queue。"""
        global _queue_counter
        if sem.locked():
            _queue_counter += 1
            pos = _queue_counter
            await event_queue.put({
                "type": "queued",
                "queue_position": pos,
            })

        async with sem:
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
                            "_event_queue": collector,
                        },
                        thread_id=thread_id,
                    ),
                    timeout=settings.supervisor_timeout,
                )

                if reply.type == "error":
                    collector.fail_running(reply.payload.get("error", "Agent error"))
                    await event_queue.put({
                        "type": "error",
                        "error": reply.payload.get("error", "Agent error"),
                        "detail": reply.payload.get("error", "Agent error"),
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
                    agents=agents, model=body.model,
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
                logger.exception("multi-agent stream invocation failed")
                service.update(user_id, child_id, status="error")
                collector.fail_running(str(e))
                await event_queue.put({
                    "type": "error",
                    "error": str(e),
                    "detail": str(e),
                    **classify_error(e),
                })
            finally:
                task_bridge.unregister(child_id)

    async def event_generator():
        """生成 SSE 事件流。"""
        task = asyncio.create_task(run_multi_agent())
        try:
            while True:
                event = await event_queue.get()
                # 注入 conversation_id：前端在流中尽早拿到会话 id，
                # 使"停止"按钮能调用 /api/sessions/{id}/interrupt 真正打断后台任务
                event["conversation_id"] = session_id
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in ("done", "error"):
                    break
            await task
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        # 响应头尽早透出会话 id：前端在读取任何 SSE 事件前即可记录 conversation_id，
        # 使"停止/撤销"按钮在任何时刻都能 POST /interrupt 真正打断后台 Agent 任务
        headers={"X-Session-Id": session_id},
    )


@router.post("/stream")
async def chat_stream(request: Request, body: ChatRequest):
    """处理流式聊天请求，返回SSE事件流。

    设计文档 P3：消息经 SessionService 落库 session.db，Agent 执行由
    per-session 协调器串行调度；conversation_id 即 session.id。
    """
    user_id = _get_user_id(request)
    service = _get_session_service(request)

    # 解析/创建会话（conversation_id == session.id）
    session_id = body.conversation_id
    if session_id:
        try:
            service.get(user_id, session_id)
        except session_repo.SessionNotFound:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        session = service.create(user_id, directory=discover_project_root(), kind="chat")
        session_id = session.id

    # 请求级事件桥（request_id 不入库，executor 按此回填事件队列）
    request_id = uuid.uuid4().hex
    event_queue: asyncio.Queue = asyncio.Queue()
    register_request_queue(request_id, event_queue)

    # 会话正在执行 → 先发 queued 事件（对齐旧 _agent_semaphore 排队 UX）
    active = session_id in service.coordinator.active_sessions
    pending = session_repo.count_pending(session_id) if active else 0

    service.prompt(
        user_id, session_id, body.message,
        files=[f.model_dump() for f in body.files],
        delivery="queue",
        model=body.model,
        use_vector_db=body.use_vector_db,
        request_id=request_id,
    )

    async def event_generator():
        try:
            if active:
                yield f"data: {json.dumps({'type': 'queued', 'queue_position': pending + 1, 'conversation_id': session_id}, ensure_ascii=False)}\n\n"
            while True:
                event = await event_queue.get()
                # 注入 conversation_id：前端在流中尽早拿到会话 id，
                # 使"停止"按钮能调用 /api/sessions/{id}/interrupt 真正打断后台任务
                event["conversation_id"] = session_id
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in ("done", "error"):
                    break
        finally:
            unregister_request_queue(request_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        # 响应头尽早透出会话 id：前端在读取任何 SSE 事件前即可记录 conversation_id，
        # 使"停止/撤销"按钮在任何时刻都能 POST /interrupt 真正打断后台 Agent 任务
        headers={"X-Session-Id": session_id},
    )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, request: Request):
    """删除指定对话（只能删除自己的对话）。"""
    user_id = _get_user_id(request)
    service = _get_session_service(request)
    try:
        service.remove(user_id, conversation_id)
        return {"status": "ok"}
    except session_repo.SessionNotFound:
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.delete("/conversations/{conversation_id}/messages/{message_id}")
async def delete_message(conversation_id: str, message_id: str, request: Request):
    """删除对话中的指定消息（只能删除自己的对话）。"""
    user_id = _get_user_id(request)
    service = _get_session_service(request)
    try:
        service.get(user_id, conversation_id)
    except session_repo.SessionNotFound:
        raise HTTPException(status_code=404, detail="Conversation not found")
    except session_repo.Forbidden:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not session_repo.delete_message(conversation_id, message_id):
        raise HTTPException(status_code=404, detail="Message not found")
    return {"status": "ok"}


@router.get("/stream/status")
async def stream_status(request: Request):
    """返回当前并发状态，前端可轮询。
    P3 起 /stream 由 SessionCoordinator 调度：active = 正在执行的会话数，
    queue_depth = 各活动会话待执行输入数（替代旧全局 _agent_semaphore）。
    """
    service = _get_session_service(request)
    active = len(service.coordinator.active_sessions)
    depth = sum(session_repo.count_pending(sid) for sid in service.coordinator.active_sessions)
    return {
        "max_concurrent": MAX_CONCURRENT_AGENTS,
        "active": active,
        "queue_depth": depth,
    }


@router.get("/conversations")
async def list_conversations(request: Request, conv_type: str | None = None):
    """获取当前用户的对话列表，按更新时间倒序排列。
    支持 ?conv_type=chat 或 ?conv_type=multi-agent 过滤。
    """
    user_id = _get_user_id(request)
    service = _get_session_service(request)
    kind = conv_type if conv_type in ("chat", "multi-agent") else None
    sessions = service.list_sessions(user_id, archived=False, limit=1000)
    result = [
        {"id": s.id, "title": s.title or "新对话", "created_at": _fmt_time(s.time_created),
         "updated_at": _fmt_time(s.time_updated)}
        for s in sessions
        if (kind is None or s.kind == kind)
    ]
    result.sort(key=lambda r: r["updated_at"], reverse=True)
    return result


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request, conv_type: str | None = None):
    """获取指定对话的详细信息和消息历史（只能查看自己的对话）。"""
    user_id = _get_user_id(request)
    service = _get_session_service(request)
    try:
        session = service.get(user_id, conversation_id)
    except session_repo.SessionNotFound:
        raise HTTPException(status_code=404, detail="Conversation not found")
    except session_repo.Forbidden:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv_type and session.kind != conv_type:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = []
    for m in service.messages(user_id, conversation_id):
        msg = {
            "id": m.id,
            "role": _msg_type_to_role(m.type),
            "content": m.data.get("content", ""),
            "seq": m.seq,
        }
        if m.data.get("sources"):
            msg["sources"] = m.data["sources"]
        if m.data.get("steps"):
            msg["steps"] = m.data["steps"]
        if m.data.get("agents"):
            msg["agents"] = m.data["agents"]
        parts = session_repo.list_parts(m.id)
        if parts:
            msg["parts"] = [p.model_dump() for p in parts]
        messages.append(msg)
    return {
        "id": conversation_id,
        "title": session.title or "新对话",
        "messages": messages,
        "created_at": _fmt_time(session.time_created),
        "updated_at": _fmt_time(session.time_updated),
    }


@router.put("/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, body: dict, request: Request):
    """更新对话标题（只能更新自己的对话）。"""
    user_id = _get_user_id(request)
    title = body.get("title")
    if title is None:
        raise HTTPException(status_code=400, detail="title is required")
    service = _get_session_service(request)
    try:
        service.update(user_id, conversation_id, title=title)
        return {"status": "ok"}
    except session_repo.SessionNotFound:
        raise HTTPException(status_code=404, detail="Conversation not found")
    except session_repo.Forbidden:
        raise HTTPException(status_code=404, detail="Conversation not found")
