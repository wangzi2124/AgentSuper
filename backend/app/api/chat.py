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

# --- 用户身份：优先从 X-User-Id 头获取，默认 anonymous ---
_DEFAULT_USER_ID = "anonymous"

def _get_user_id(request: Request) -> str:
    """从请求头中提取用户身份。

    B12: 记录 (user_id, 来源 IP) 审计日志。
    """
    uid = request.headers.get("X-User-Id", "")
    user_id = uid.strip() if uid.strip() else _DEFAULT_USER_ID
    client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )
    logger.debug("user_id=%s ip=%s path=%s", user_id, client_ip, request.url.path)
    return user_id

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


# [token 优化 v3] Sliding window: keep up to 32K tokens of history before passing to Agent.
# graph.py 通过 config.max_context_tokens(v9 后为 24K，usable ≈ 15.8K) 做上下文管理，此阈值仅控制历史注入量。
# [token 优化 v9] 32K → 16K：历史注入量减半，长会话首条消息不再重发整段 32K 历史。
# [B9] 历史注入预算与上下文配置联动：min(16K, usable_context_tokens())。
#   usable 缩小时（reserve 变大）预算自动缩小，防止注入量超出可用上下文；
#   usable 放大时仍封顶 16K，不扩大既有行为。消除与 budget.py 的双常量漂移。
MAX_HISTORY_TOKENS = max(1, min(16_000, usable_context_tokens()))

# 聊天消息长度上限（与前端 ChatInput.vue 的 MAX_LENGTH 对齐，前端+后端双层约束）
MAX_MESSAGE_LENGTH = 50_000

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
    """根据用户第一条消息生成对话标题。

    B8: 清洗控制字符 + html.escape 防注入，字节安全截断。
    """
    import html
    import re
    for msg in messages:
        if msg.get("role") == "user":
            text = msg.get("content", "")
            # 去除控制字符（保留换行/制表）
            text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
            text = text.strip().replace("\n", " ")
            text = html.escape(text)
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


# ═══════════════════════════════════════════════════════════════
#  Multi-Agent 子任务会话（设计文档 P4）：
#  每次 send_and_wait 登记为 kind='task' 子会话，级联取消经 task_bridge
# ═══════════════════════════════════════════════════════════════

def _resolve_multi_agent_parent(request: Request, user_id: str, conv_id: str | None, directory: str = "") -> tuple[object, str, str]:
    """解析/创建 multi-agent 主会话（session.db）。返回 (service, session_id, session_directory)。"""
    service = _get_session_service(request)
    if conv_id:
        try:
            info = service.get(user_id, conv_id)
        except session_repo.SessionNotFound:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return service, conv_id, info.directory or ""
    else:
        session = service.create(user_id, directory=directory or discover_project_root(), kind="multi-agent")
        return service, session.id, session.directory or ""


def _session_history_for(service, user_id: str, session_id: str, limit: int = 200) -> list[dict]:
    """把会话消息投影为模型历史（role/content）。

    正文优先从 message_parts 的 text part 提取（对齐设计 §3），无 parts 时
    回退 data.content（旧数据/compaction/system 消息）。

    B3: 限制拉取条数（默认 200），避免长会话 O(n) 全量拉取 + 全量 parts 查询。
    """
    from app.session import history as session_history

    messages = service.messages(user_id, session_id, limit=limit)
    ids = [m.id for m in messages]
    parts_map = session_repo.list_parts_for_messages(ids) if ids else {}
    history = []
    for m in messages:
        role = m.data.get("role") or _msg_type_to_role(m.type)
        if role in ("user", "assistant", "system"):
            content = session_history.text_from_parts(parts_map.get(m.id, [])) or m.data.get("content", "")
            history.append({"role": role, "content": content})
    return history


async def _build_compressed_history(service, user_id: str, session_id: str) -> list[dict]:
    """[B10] 抽取历史加载 + 压缩 + 清洗的公共逻辑，避免两个多 Agent 端点重复。

    启用 SummarizationMiddleware 时用 LLM 分层压缩，否则按 token 截断；
    最终统一 _sanitize_history 清洗。
    """
    history = _session_history_for(service, user_id, session_id)
    summarizer = _get_summarizer()
    if summarizer:
        compressed = await summarizer.apply(history)
    else:
        compressed = _truncate_history(history)
    return _sanitize_history(compressed)


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
                               agents: list | None = None, model: str | None = None,
                               tokens: dict | None = None, client_msg_id: str | None = None,
                               files: list | None = None) -> tuple[str, str]:
    """主会话 + 子任务会话各追加 user/assistant 消息；新会话生成标题。

    主会话写经 write_lock 串行化（与 /stream 协调器执行体、compact/revert 互斥），
    保证同一会话的消息顺序不被交错。

    [token 优化 v9] tokens 参数：supervisor 汇总的本次请求真实 LLM 用量
    （分解 + 子 Agent + 汇总），与单 Agent executor 落库口径对齐，供前端/DB 展示。

    [B4] client_msg_id 幂等：前端自动/手动重试复用同一 client_msg_id，
    主/子会话均按 (user_id, session_id, client_msg_id) 去重——命中已落库的完整
    轮次直接复用 id、跳过写入；命中 user 但缺 assistant 时补齐缺失对，避免断网
    重试/重复请求产生重复轮次。
    """
    async with service.write_lock(session_id):
        user_msg_id, existing_assistant_id = _existing_pair(service, user_id, session_id, client_msg_id)
        if not existing_assistant_id:
            if user_msg_id is None:
                user_msg = service.append_message(user_id, session_id, "user", {
                    "role": "user", "content": question, "client_msg_id": client_msg_id,
                    "files": files or [],
                })
                user_msg_id = user_msg.id
                if session_repo.latest_seq(session_id) == 1:
                    service.update(user_id, session_id, title=_generate_title([{"role": "user", "content": question}]))
            # 新写入或 user 命中但缺 assistant：追加 assistant 轮次
            assistant_msg = service.append_message(user_id, session_id, "assistant", {
                "role": "assistant", "content": answer, "sources": sources, "steps": steps,
                "agents": agents or [], "parent_id": user_msg_id, "agent": "supervisor", "model": model,
                "tokens": tokens or {},
            })
            _persist_multi_agent_parts(session_id, assistant_msg.id, answer, agents)
            assistant_msg_id = assistant_msg.id
        else:
            # [B4] 完整轮次已落库 → 直接复用 id，不重复写入主会话
            assistant_msg_id = existing_assistant_id
    # 子任务会话独立日志（隔离上下文，幂等去重）
    # [B13] 主会话已先行提交（独立 write_lock + append_message 各自事务），
    # 子会话写入用 try/except 兜底：即使子会话落库失败也只记日志，不回滚、
    # 不中断主会话已落库的完整轮次，保证「主会话内容不因子会话故障而丢失」。
    try:
        async with service.write_lock(child_id):
            _ensure_child_pair(service, user_id, child_id, question, answer, sources,
                               steps, agents, model, tokens, client_msg_id)
    except Exception:
        logger.exception("Failed to persist child session %s (main %s), main already committed", child_id, session_id)
    return user_msg_id, assistant_msg_id


def _existing_pair(service, user_id: str, session_id: str,
                   client_msg_id: str | None) -> tuple[str | None, str | None]:
    """[B4] 按 client_msg_id 查找已落库的 user/assistant 对。

    返回 (user_msg_id, assistant_msg_id)；assistant 缺失时第二个元素为 None，
    供调用方决定补齐；client_msg_id 为空或未命中时返回 (None, None)。
    """
    if not client_msg_id:
        return None, None
    msgs = service.messages(user_id, session_id)
    for i, m in enumerate(msgs):
        if m.type == "user" and m.data.get("client_msg_id") == client_msg_id:
            for a in msgs[i + 1:]:
                if a.type == "assistant":
                    # [B11] 中断的部分 assistant 不算完整轮次：
                    # 返回 (user_id, None) 让重试继续补齐，而不是复用残缺答案
                    if a.data.get("interrupted"):
                        return m.id, None
                    return m.id, a.id
            return m.id, None
    return None, None


def _ensure_child_pair(service, user_id: str, session_id: str, question: str, answer: str,
                       sources: list, steps: list, agents: list | None, model: str | None,
                       tokens: dict | None, client_msg_id: str | None) -> None:
    """[B4] 确保子任务会话存在与主会话一致的 user/assistant 对（幂等）。"""
    user_msg_id, existing_assistant_id = _existing_pair(service, user_id, session_id, client_msg_id)
    if existing_assistant_id:
        return
    if user_msg_id is None:
        user_msg = service.append_message(user_id, session_id, "user", {
            "role": "user", "content": question, "client_msg_id": client_msg_id,
        })
        user_msg_id = user_msg.id
    child_assist = service.append_message(user_id, session_id, "assistant", {
        "role": "assistant", "content": answer, "sources": sources, "steps": steps,
        "agents": agents or [], "parent_id": user_msg_id, "agent": "supervisor", "model": model,
        "tokens": tokens or {},
    })
    _persist_multi_agent_parts(session_id, child_assist.id, answer, agents)
    service.update(user_id, session_id, status="idle")


async def _persist_interrupted_partial(service, user_id: str, session_id: str, child_id: str,
                                       question: str, answer: str, agents: list | None,
                                       client_msg_id: str | None) -> None:
    """[B11] 会话中断时把已生成的部分内容落库（status=interrupted）。

    客户端断开/取消导致流未走到 done 时，主/子会话并没有完整 user/assistant 轮次
    （_persist_multi_agent 只在 done 时落库）。这里在 event_generator 的 finally 兜底
    记录「用户问题 + 部分回答 + 子 Agent 快照」，使用户重开历史能恢复已产出的内容。

    assistant 标记 interrupted=True：B4 的 _existing_pair 会将其视为「未完整」，
    因此前端以同一 client_msg_id 自动重试时仍会补齐完整轮次，不会复用残缺答案。
    """
    try:
        def _append_partial(target_session: str) -> str:
            user_msg_id, existing_assistant_id = _existing_pair(service, user_id, target_session, client_msg_id)
            if existing_assistant_id:
                return existing_assistant_id
            if user_msg_id is None:
                user_msg = service.append_message(user_id, target_session, "user", {
                    "role": "user", "content": question, "client_msg_id": client_msg_id,
                })
                user_msg_id = user_msg.id
            partial = service.append_message(user_id, target_session, "assistant", {
                "role": "assistant", "content": answer, "sources": [], "steps": [],
                "agents": agents or [], "parent_id": user_msg_id, "agent": "supervisor",
                "model": None, "tokens": {}, "interrupted": True,
            })
            _persist_multi_agent_parts(target_session, partial.id, answer, agents)
            return partial.id

        async with service.write_lock(session_id):
            _append_partial(session_id)
        async with service.write_lock(child_id):
            _append_partial(child_id)
            service.update(user_id, child_id, status="interrupted")
    except Exception:
        logger.exception("persist interrupted partial failed: user=%s session=%s", user_id, session_id)


def _validate_chat_message(body: ChatRequest) -> None:
    """聊天消息兜底校验：空内容/超长直接 422（与 schema 约束双层防护）。

    schema 的 Field(min_length/max_length) 已拦截超长输入，此处兜底处理
    纯空白消息（len>0 但 strip 后为空）并给出友好提示。
    """
    msg = body.message
    if not msg or not msg.strip():
        raise HTTPException(status_code=422, detail="消息内容不能为空")
    if len(msg) > MAX_MESSAGE_LENGTH:
        raise HTTPException(status_code=422, detail=f"消息长度超出上限（{MAX_MESSAGE_LENGTH} 字符）")



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