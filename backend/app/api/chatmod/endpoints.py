"""拆分模块 `endpoints`（含 MAX_CONCURRENT_AGENTS、MAX_QUEUE_SIZE、_agent_semaphore、_get_agent_semaphore、_queue_counter、chat_multi_agent、chat_multi_agent_stream、router）。

原文件 docstring: 聊天 API 路由模块。

提供聊天对话的创建、流式响应、历史记录管理等功能。"""

# ── 复制自原模块的顶层 import ──

import asyncio

import json

import logging


from fastapi import APIRouter, HTTPException, Request

from fastapi.responses import StreamingResponse

from app.config import settings




from app.models.schemas import ChatRequest, Source, StepEvent, MultiAgentChatResponse
from app.models.catalog import model_ref_dict


from app.session import task_bridge

from app.session.agent_executor import classify_error, friendly_chat_error, PartBridgeQueue


from app.agent.base import AgentMessage

from app.agent.bus import AgentBus

from app.agent.stream_events import AgentEventCollector

# ── 跨子模块依赖（自动生成）──

from .helpers import _get_user_id
from .helpers import _prompt_to_chat_request
from .helpers import _validate_chat_message
from .persist import _begin_task_session
from .persist import _build_compressed_history
from .persist import _persist_interrupted_partial
from .persist import _persist_multi_agent
from .persist import _resolve_multi_agent_parent
from .snapshot_diff import _abort_turn, _before_hash, _files_changed
from app.snapshot import turn as snap_turn

from app.session import repository as session_repo
from app.snapshot.turn import diff_turn, restore_session_turn

from app.models.schemas import DiffRequest, RestoreSnapshotRequest

logger = logging.getLogger(__name__)

# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──

"""聊天 API 路由模块。

提供聊天对话的创建、流式响应、历史记录管理等功能。
"""

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import settings

from app.models.schemas import ChatRequest, Source, StepEvent, MultiAgentChatResponse

# ── Session 管理（session.db）──
from app.session import task_bridge
from app.session.agent_executor import classify_error, friendly_chat_error, PartBridgeQueue

# ── 多 Agent 系统 ──
from app.agent.base import AgentMessage
from app.agent.bus import AgentBus
from app.agent.stream_events import AgentEventCollector

logger = logging.getLogger(__name__)


# ── 全链路日志（app/chainlog）────────────────────────────────────────────
# 一次请求的链路节点：chat.received → session.resolved → history.ready →
# agent.dispatch → (sub-agent 内部节点) → agent.reply → persist.done → chat.done。
# trace 上下文由 ChainLogMiddleware 建立，这里把会话/子任务等后知信息补进上下文，
# 并把上下文 attach 进 AgentMessage.payload，让子 Agent 落在同一条链路上。

def _chainlog_trace_fields(session_id: str = "", child_id: str = "") -> dict:
    """把会话信息补进当前 trace 上下文（供后续所有节点自动携带 session_id）。"""
    from app import chainlog

    chainlog.set_fields(session_id=session_id)
    return chainlog.payload_trace()


router = APIRouter()


def _sync_session_model(service, user_id: str, session_id: str, model) -> bool:
    """[模型切换] 请求携带的 model 与会话当前 model 不同则持久化（对齐 opencode switchModel）。

    返回是否发生了切换；流式路径据此推送 model_switched SSE 事件。
    DB 的 ModelRef 规范为 {id, providerID, variant}（camelCase），而 SSE 事件使用
    {id, provider, name}（model_ref_dict 格式，给前端渲染用）。
    """
    if not model:
        return False
    try:
        info = service.get(user_id, session_id)
    except Exception:
        return False
    current_id = getattr(info.model, "id", None)
    if current_id == model:
        return False
    provider_id = model.split("/", 1)[0] if "/" in model else ""
    new_ref = {"id": model, "providerID": provider_id, "variant": None}
    try:
        service.update(user_id, session_id, model=new_ref)
    except Exception:
        logger.exception("sync session model failed for %s", session_id)
        return False
    return True


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
    _sync_session_model(service, user_id, session_id, body.model)

    from app import chainlog
    chainlog.set_fields(session_id=session_id, user_id=user_id)
    chainlog.info(
        "http", "chat.multi_agent", "chat.received",
        message=f"非流式请求：{body.message[:80]}",
        data={
            "stream": False, "session_id": session_id, "user_id": user_id,
            "model": body.model, "agent_mode": body.agent_mode,
            "use_vector_db": body.use_vector_db, "directory": session_dir,
            "file_count": len(body.files or []),
            "has_voice": bool(body.voice),
            "message_chars": len(body.message),
        },
    )
    _chainlog_trace_fields(session_id)

    compressed = await _build_compressed_history(service, user_id, session_id)
    chainlog.info(
        "session", "chat.multi_agent", "history.ready",
        message=f"装配历史 {len(compressed)} 条",
        data={"history_messages": len(compressed)},
    )

    # 请求级事件收集器：非流式路径无 SSE 消费端，但必须注入 _event_queue，
    # 否则子 Agent 触发 NeedsPermission 时会因「无事件队列」直接拒绝（连弹窗/审批都没有）。
    # 事件会记录在 collector 副本中供 agents_snapshot 落库；审批弹窗由外部客户端
    # 轮询 GET /api/permission/pending 呈现（前端流式路径仍走 SSE permission_request）。
    _event_queue: asyncio.Queue = asyncio.Queue()
    collector = AgentEventCollector(_event_queue)

    # 登记子任务会话（kind='task'）+ AgentBus thread
    child_id, thread_id = _begin_task_session(service, user_id, session_id, body.message)
    chainlog.info(
        "session", "chat.multi_agent", "task.registered",
        message=f"登记子任务会话 {child_id}",
        data={"child_id": child_id, "thread_id": thread_id,
              "parent_session_id": session_id},
    )

    # [文件改动] 请求开始前拍快照（before tree），完成后 diff 出本次变更文件
    before_hash = _before_hash(request)

    try:
        # 通过 Supervisor 发送请求
        # 如果指定了 agent_mode（顶层命令仅 plan），直接发送到 plan Agent
        target_agent = "supervisor"
        if body.agent_mode == "plan":
            target_agent = body.agent_mode
        # 把 trace 上下文注入 payload，随消息透传到 supervisor 与全部子 Agent
        _trace = _chainlog_trace_fields(session_id, child_id)

        reply = await agent_bus.send_and_wait(
            AgentMessage(
                source="user",
                target=target_agent,
                type="request",
                action="chat",
                payload={
                    "question": body.message,
                    "model": body.model,
                    "history": compressed,
                    "use_vector_db": body.use_vector_db,
                    "files": [f.model_dump() for f in body.files],
                    "voice": body.voice.model_dump() if body.voice else None,
                    "conversation_id": session_id,
                    "user_id": user_id,
                    "directory": session_dir,
                    "_event_queue": collector,
                    "agent_mode": body.agent_mode,
                    chainlog.TRACE_PAYLOAD_KEY: _trace,
                    # [轮次快照] 见 stream 端点同注释：跨事件循环 task 传递活跃 turn
                    snap_turn.TURN_PAYLOAD_KEY: snap_turn.active_turn(),
                },
                thread_id=thread_id,
            ),
            timeout=settings.supervisor_timeout,
        )
    except asyncio.CancelledError:
        # [opencode abort 级联] 客户端断开 → 除登记清理外，中断 supervisor 在途 handler
        abort = getattr(agent_bus, "abort", None)
        if abort is not None:
            abort(thread_id)
        task_bridge.unregister(child_id)
        service.update(user_id, child_id, status="interrupted")
        chainlog.warning(
            "http", "chat.multi_agent", "chat.cancelled",
            message="客户端断开（499）",
            data={"child_id": child_id, "thread_id": thread_id},
        )
        _abort_turn()
        raise HTTPException(status_code=499, detail="Request cancelled")
    except Exception as e:
        task_bridge.unregister(child_id)
        service.update(user_id, child_id, status="error")
        logger.exception("multi-agent request failed: user=%s session=%s classified=%s",
                         user_id, session_id, classify_error(e))
        chainlog.error(
            "http", "chat.multi_agent", "chat.error",
            message=f"请求失败: {friendly_chat_error(e, model=body.model)}",
            data={
                "child_id": child_id, "thread_id": thread_id,
                "error": str(e), "error_type": type(e).__name__,
                "classified": classify_error(e),
            },
        )
        _abort_turn()
        raise HTTPException(status_code=500, detail=friendly_chat_error(e, model=body.model))

    # [plan→build] build 失败时 _merge_plan_build_reply 会把已生成的计划塞进 payload["answer"]。
    # 该分支不得丢弃它 —— 否则用户只剩一句「执行失败」，规划成果凭空消失。
    _partial_answer = str((reply.payload or {}).get("answer", "") or "")
    if reply.type == "error":
        if not _partial_answer:
            task_bridge.unregister(child_id)
            service.update(user_id, child_id, status="error")
            _err_detail = (reply.payload or {}).get("error", "")
            logger.error("multi-agent reply error: user=%s session=%s detail=%s",
                         user_id, session_id, _err_detail)
            chainlog.error(
                "agent", "chat.multi_agent", "agent.reply_error",
                message=f"子 Agent 返回错误: {_err_detail}",
                data={
                    "child_id": child_id,
                    "error": _err_detail,
                    "error_type": (reply.payload or {}).get("error_type"),
                    "completed_steps": (reply.payload or {}).get("completed_steps", []),
                },
            )
            _abort_turn()
            raise HTTPException(status_code=500, detail=friendly_chat_error(
                RuntimeError(_err_detail) if _err_detail else None, model=body.model,
            ))
        # 计划已成、执行出错：回滚执行阶段的文件改动，但保留计划正文走正常落库/返回路径
        service.update(user_id, child_id, status="error")
        _abort_turn()
        chainlog.warning(
            "agent", "chat.multi_agent", "agent.reply_partial_error",
            message="子 Agent 返回错误但带有部分答案（已保留）",
            data={
                "child_id": child_id,
                "error": (reply.payload or {}).get("error"),
                "error_type": (reply.payload or {}).get("error_type"),
                "answer_chars": len(_partial_answer),
                "plan_path": (reply.payload or {}).get("plan_path"),
            },
        )

    payload = reply.payload
    answer = payload.get("answer", "")
    sources = payload.get("sources", [])
    steps = payload.get("steps", [])
    routed_to = payload.get("routed_to")
    chainlog.info(
        "agent", "chat.multi_agent", "agent.reply",
        message=f"子 Agent 回复（路由到 {routed_to}，{len(answer)} 字）",
        data={
            "routed_to": routed_to, "answer_chars": len(answer),
            "sources": len(sources), "steps": len(steps),
            "tokens": payload.get("tokens") or {},
            "cost": payload.get("cost") or 0.0,
        },
    )

    # [文件改动] 完成后对比 before/after，得到本次轮次的变更文件 + 行数 + 恢复描述
    files_changed, snapshot_restore = _files_changed(request, before_hash)

    # 落库：主会话 + 子任务会话
    user_msg_id, assistant_msg_id = await _persist_multi_agent(
        service, user_id, session_id, child_id, body.message, answer, sources, steps,
        model=body.model, tokens=payload.get("tokens"), cost=payload.get("cost") or 0.0,
        client_msg_id=body.client_msg_id,
        files=[f.model_dump() for f in body.files],
        voice=body.voice.model_dump() if body.voice else None,
        files_changed=files_changed,
        snapshot_restore=snapshot_restore,
    )
    task_bridge.unregister(child_id)
    chainlog.info(
        "persist", "chat.multi_agent", "persist.done",
        message="消息已落库",
        data={
            "user_msg_id": user_msg_id, "assistant_msg_id": assistant_msg_id,
            "files_changed": files_changed,
        },
    )
    chainlog.info(
        "http", "chat.multi_agent", "chat.done",
        message="非流式请求完成",
        data={"answer_chars": len(answer), "routed_to": routed_to,
              "files_changed": len(files_changed or [])},
    )

    return MultiAgentChatResponse(
        answer=answer,
        sources=[Source(**s) if isinstance(s, dict) else s for s in sources],
        conversation_id=session_id,
        steps=[StepEvent(**s) if isinstance(s, dict) else s for s in steps],
        routed_to=routed_to,
        files_changed=files_changed,
        plan_path=payload.get("plan_path") or None,
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

    from app import chainlog
    chainlog.set_fields(session_id=session_id, user_id=user_id)
    chainlog.info(
        "http", "chat.stream", "chat.received",
        message=f"流式请求：{body.message[:80]}",
        data={
            "stream": True, "session_id": session_id, "user_id": user_id,
            "model": body.model, "agent_mode": body.agent_mode,
            "use_vector_db": body.use_vector_db, "directory": session_dir,
            "file_count": len(body.files or []),
            "has_voice": bool(body.voice),
            "message_chars": len(body.message),
        },
    )
    _chainlog_trace_fields(session_id)

    compressed = await _build_compressed_history(service, user_id, session_id)
    chainlog.info(
        "session", "chat.stream", "history.ready",
        message=f"装配历史 {len(compressed)} 条",
        data={"history_messages": len(compressed)},
    )

    event_queue: asyncio.Queue = asyncio.Queue()
    sem = _get_agent_semaphore()
    # 请求级事件收集器：子 Agent 的实时事件经此转发到 SSE + 记录副本（落库）
    collector = AgentEventCollector(event_queue)
    if _sync_session_model(service, user_id, session_id, body.model):
        # [D3] 字段名与前端 store 对齐：`model_ref`（{id, provider, name}）。
        # 旧写法发 `model`，而 frontend/src/stores/multiAgent.ts 读的是
        # `event.model_ref` → 切模型后 store 里的 currentModel 永远不会更新。
        await event_queue.put({"type": "model_switched", "model_ref": model_ref_dict(body.model)})

    # 登记子任务会话（kind='task'）+ AgentBus thread
    child_id, thread_id = _begin_task_session(service, user_id, session_id, body.message)
    chainlog.info(
        "session", "chat.stream", "task.registered",
        message=f"登记子任务会话 {child_id}",
        data={"child_id": child_id, "thread_id": thread_id,
              "parent_session_id": session_id},
    )

    class _TurnAborted(Exception):
        """回合内部已发出终态事件（error），外层 drain 循环据此收场。"""

    # [执行中追加任务] 当前回合的 collector / 子会话引用 —— 供 event_generator 的
    # 断连兜底读取（drain 会逐轮替换成新回合的）。
    turn_ref: dict = {"collector": collector}

    async def _run_one_turn(turn_body, turn_child_id: str, turn_thread_id: str,
                            turn_history: list, turn_collector):
        """跑一个完整回合：routing → send_and_wait → 落库 → 发终态事件 → 认领下一个排队任务。

        队列为空时发 `done` 让前端断流；有排队任务时发 `turn_done`，同一条 SSE 流继续
        承载下一个排队回合。返回**已认领的下一个排队任务**（dict）或 None（队列空/本轮
        非正常结束）。

        [队列竞态] 这里刻意用「原子 pop」而不是「先 count 再 pop」：回合执行期间用户可能
        追加任务，count-then-act 会在 count=0 与 pop 之间漏掉新入队任务（或反过来多发
        一次 `done` 让前端断流、drain 出来的回合没人接收）。pop 是 repository 层单条
        事务，pop 之后才到达的任务留给下一轮用户消息处理 —— 这与 opencode「本轮结束后
        drain 期间入队的任务」语义一致，且不会丢任务。
        """
        await event_queue.put({
            "type": "routing",
            "detail": "正在分析问题并选择最合适的 Agent...",
        })

        # 如果指定了 agent_mode（顶层命令仅 plan），直接发送到 plan Agent
        target_agent = "supervisor"
        if turn_body.agent_mode == "plan":
            target_agent = turn_body.agent_mode

        # [文件改动] 请求开始前拍快照（before tree），完成后 diff 变更文件
        before_hash = _before_hash(request)
        # 把 trace 上下文注入 payload，随消息透传到 supervisor 与全部子 Agent
        _trace = _chainlog_trace_fields(session_id, turn_child_id)

        reply = await agent_bus.send_and_wait(
            AgentMessage(
                source="user",
                target=target_agent,
                type="request",
                action="chat",
                payload={
                    "question": turn_body.message,
                    "model": turn_body.model,
                    "history": turn_history,
                    "use_vector_db": turn_body.use_vector_db,
                    "files": [f.model_dump() for f in turn_body.files],
                    "voice": turn_body.voice.model_dump() if turn_body.voice else None,
                    "conversation_id": session_id,
                    "user_id": user_id,
                    "directory": session_dir,
                    "_event_queue": turn_collector,
                    "agent_mode": turn_body.agent_mode,
                    chainlog.TRACE_PAYLOAD_KEY: _trace,
                    # [轮次快照] 事件循环 task 在启动时创建，contextvar 传不进去
                    # （同 chainlog），把活跃 turn 挂进 payload 由 bus._dispatch 重绑
                    snap_turn.TURN_PAYLOAD_KEY: snap_turn.active_turn(),
                },
                thread_id=turn_thread_id,
            ),
            timeout=settings.supervisor_timeout,
        )

        # [plan→build] build 失败但计划已生成：payload["answer"] 带着完整计划。
        # 旧实现把它连同 plan_path 一起丢掉，用户只剩一句「执行失败」。
        _err_payload = reply.payload or {}
        _partial_answer = str(_err_payload.get("answer", "") or "")
        if reply.type == "error" and not _partial_answer:
            _err_detail = _err_payload.get("error", "")
            logger.error("multi-agent reply error: session=%s detail=%s",
                         session_id, _err_detail)
            generic_error = friendly_chat_error(
                RuntimeError(_err_detail) if _err_detail else None, model=turn_body.model,
            )
            chainlog.error(
                "agent", "chat.stream", "agent.reply_error",
                message=f"子 Agent 返回错误: {generic_error}",
                data={
                    "child_id": turn_child_id, "error": _err_detail,
                    "error_type": _err_payload.get("error_type"),
                    "completed_steps": _err_payload.get("completed_steps", []),
                },
            )
            _abort_turn()
            turn_collector.fail_running(generic_error)
            await event_queue.put({
                "type": "error",
                "error": generic_error,
                "detail": generic_error,
                "retryable": False,
                "status_code": None,
                "error_type": "AgentError",
            })
            raise _TurnAborted

        payload = reply.payload
        answer = payload.get("answer", "")
        sources = payload.get("sources", [])
        steps = payload.get("steps", [])
        routed_to = payload.get("routed_to")
        # 执行出错但有部分答案 → 计划正文同样要落库，刷新后不丢
        partial_error = bool(reply.type == "error")
        if partial_error:
            service.update(user_id, turn_child_id, status="error")
            _abort_turn()
            turn_collector.fail_running(str(_err_payload.get("error") or "执行出错"))
            chainlog.warning(
                "agent", "chat.stream", "agent.reply_partial_error",
                message="子 Agent 返回错误但带有部分答案（已保留并落库）",
                data={
                    "child_id": turn_child_id,
                    "error": _err_payload.get("error"),
                    "error_type": _err_payload.get("error_type"),
                    "answer_chars": len(answer),
                    "plan_path": _err_payload.get("plan_path"),
                },
            )
        agents = turn_collector.agents_snapshot()
        chainlog.info(
            "agent", "chat.stream", "agent.reply",
            message=f"子 Agent 回复（路由到 {routed_to}，{len(answer)} 字）",
            data={
                "routed_to": routed_to, "answer_chars": len(answer),
                "sources": len(sources), "steps": len(steps),
                "agents": [
                    {"agent_id": a.get("agent_id"),
                     "status": a.get("status"),
                     "steps": len(a.get("steps") or [])}
                    for a in agents
                ],
                "tokens": payload.get("tokens") or {},
                "cost": payload.get("cost") or 0.0,
            },
        )

        # [文件改动] 完成后对比 before/after，得到本次轮次的变更文件 + 行数 + 恢复描述
        files_changed, snapshot_restore = _files_changed(request, before_hash)

        # 落库：主会话 + 子任务会话（先落库以拿到消息 id）
        user_msg_id, assistant_msg_id = await _persist_multi_agent(
            service, user_id, session_id, turn_child_id, turn_body.message, answer, sources, steps,
            agents=agents, model=turn_body.model, tokens=payload.get("tokens"),
            cost=payload.get("cost") or 0.0, client_msg_id=turn_body.client_msg_id,
            files=[f.model_dump() for f in turn_body.files],
            voice=turn_body.voice.model_dump() if turn_body.voice else None,
            files_changed=files_changed,
            snapshot_restore=snapshot_restore,
        )

        # [队列竞态] 认领下一个排队任务（原子 pop）→ 决定发 done 还是 turn_done。
        # 放在持久化之后、终态事件之前：此刻入队的任务也会被本轮流看到。
        next_prompt = await service.pop_prompt(session_id)
        final = next_prompt is None

        chainlog.info(
            "persist", "chat.stream", "persist.done",
            message="消息已落库",
            data={
                "user_msg_id": user_msg_id,
                "assistant_msg_id": assistant_msg_id,
                "files_changed": files_changed,
                "queued_turn": not final,
            },
        )

        await event_queue.put({
            "type": "done" if final else "turn_done",
            "answer": answer,
            "sources": [
                {"document_id": s["document_id"], "content": s["content"], "score": s["score"]}
                if isinstance(s, dict) else s
                for s in sources
            ],
            "conversation_id": session_id,
            "user_msg_id": user_msg_id,
            "assistant_msg_id": assistant_msg_id,
            "model": turn_body.model,
            "steps": steps,
            "routed_to": routed_to,
            "agents": agents,
            "tokens": payload.get("tokens") or {},
            "cost": payload.get("cost") or 0.0,
            "files_changed": files_changed,
            "plan_path": payload.get("plan_path") or None,
            # 排队任务续跑：该回合由队列 drain 出来，前端据此把徽标从「排队中」转为已回答
            "queued_turn": not final,
            # [队列竞态] 本回合认领到的下一个排队任务 id（None = 队列已空，发 done 断流）
            "next_prompt_id": next_prompt.get("id") if next_prompt else None,
            # [plan→build] 计划已成、执行出错：正文里已含「## 执行结果（出错）」，
            # 带上 partial_error 让前端能标红提示，但**不**走 error 事件（否则正文被覆盖）。
            "partial_error": _err_payload.get("error") if partial_error else None,
        })
        chainlog.info(
            "http", "chat.stream", "chat.done",
            message="流式请求完成" if final else "排队任务完成，继续 drain",
            data={
                "answer_chars": len(answer), "routed_to": routed_to,
                "files_changed": len(files_changed or []),
                "queued_turn": not final,
            },
        )
        # 交回已认领的排队任务；None = 队列空（调用方 break）
        return next_prompt

    async def run_multi_agent():
        """通过 Supervisor 运行多 Agent 系统，结果推送到 event_queue。

        **[执行中追加任务]** 当前轮跑完后不立刻断流：按「后来居上」顺序（新→旧）
        继续 drain 该会话的待处理任务队列（`service.pop_prompt`），每个排队任务跑一个
        完整回合并发 `turn_done`；**队列空才发终态 `done`**（前端据此断流）。
        语义 = opencode「执行中仍可发消息」+ 用户要求的后来居上插队。
        """
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
                chainlog.warning(
                    "http", "chat.stream", "request.queued",
                    message=f"并发已满，排队第 {queued_position} 位",
                    data={"queue_position": queued_position,
                          "max_concurrent_agents": MAX_CONCURRENT_AGENTS,
                          "max_queue_size": MAX_QUEUE_SIZE},
                )

            async with sem:
                if queued_position is not None:
                    _queue_counter = max(0, _queue_counter - 1)
                try:
                    turn_body = body
                    turn_child_id, turn_thread_id = child_id, thread_id
                    turn_history = compressed
                    while True:
                        # 开跑前的队列快照只用于「提示前端本轮之后还有活」，
                        # 不再据此决定 done/turn_done —— 那个决定在回合末尾原子 pop 时做。
                        pending_now = session_repo.count_pending(session_id)
                        if pending_now > 0:
                            await event_queue.put({
                                "type": "queue_drain",
                                "queue_size": pending_now,
                                "detail": f"本轮完成后继续执行 {pending_now} 个排队任务",
                            })
                        turn_collector = AgentEventCollector(event_queue)
                        turn_ref["collector"] = turn_collector
                        try:
                            nxt = await _run_one_turn(turn_body, turn_child_id, turn_thread_id,
                                                       turn_history, turn_collector)
                        except _TurnAborted:
                            return
                        except asyncio.TimeoutError:
                            _abort_turn()
                            turn_collector.fail_running("请求超时，请重试")
                            chainlog.error(
                                "http", "chat.stream", "chat.timeout",
                                message=f"supervisor 超时（{settings.supervisor_timeout:.0f}s）",
                                data={
                                    "supervisor_timeout": settings.supervisor_timeout,
                                    "child_id": turn_child_id, "thread_id": turn_thread_id,
                                },
                            )
                            await event_queue.put({
                                "type": "error",
                                "error": "请求超时，请重试",
                                "detail": "请求超时，请重试",
                                "retryable": True,
                                "status_code": None,
                                "error_type": "TimeoutError",
                            })
                            return
                        except asyncio.CancelledError:
                            service.update(user_id, turn_child_id, status="interrupted")
                            _abort_turn()
                            turn_collector.fail_running("请求已取消")
                            chainlog.warning(
                                "http", "chat.stream", "chat.cancelled",
                                message="请求已取消",
                                data={"child_id": turn_child_id, "thread_id": turn_thread_id},
                            )
                            await event_queue.put({
                                "type": "error",
                                "detail": "cancelled",
                                "retryable": False,
                                "status_code": None,
                                "error_type": "CancelledError",
                            })
                            return
                        except Exception as e:
                            logger.exception("multi-agent stream invocation failed: user=%s session=%s",
                                             user_id, session_id)
                            service.update(user_id, turn_child_id, status="error")
                            _abort_turn()
                            generic_error = friendly_chat_error(e, model=turn_body.model)
                            turn_collector.fail_running(generic_error)
                            chainlog.error(
                                "http", "chat.stream", "chat.error",
                                message=f"流式请求失败: {generic_error}",
                                data={
                                    "child_id": turn_child_id, "thread_id": turn_thread_id,
                                    "error": str(e), "error_type": type(e).__name__,
                                    "classified": classify_error(e),
                                },
                            )
                            await event_queue.put({
                                "type": "error",
                                "error": generic_error,
                                "detail": generic_error,
                                **classify_error(e),
                            })
                            return
                        finally:
                            task_bridge.unregister(turn_child_id)

                        if nxt is None:
                            break
                        # 已在 _run_one_turn 末尾原子 pop 出 nxt（最新优先），按 ChatRequest
                        # 重建一个回合；未指定的字段继承本轮请求（模型/向量库/工作目录等一致）。
                        turn_body = _prompt_to_chat_request(body, nxt.get("prompt") or {})
                        turn_child_id, turn_thread_id = _begin_task_session(
                            service, user_id, session_id, turn_body.message,
                        )
                        # 重新装配历史：此时上一轮的回答已落库，排队任务要看到它
                        turn_history = await _build_compressed_history(service, user_id, session_id)
                        chainlog.info(
                            "session", "chat.stream", "queue.drain",
                            message=f"开始执行排队任务（后来居上）: {turn_body.message[:60]}",
                            data={
                                "prompt_id": nxt.get("id"),
                                "child_id": turn_child_id,
                                "thread_id": turn_thread_id,
                                "remaining": session_repo.count_pending(session_id),
                            },
                        )
                except asyncio.CancelledError:
                    service.update(user_id, child_id, status="interrupted")
                    _abort_turn()
                    collector.fail_running("请求已取消")
                    chainlog.warning(
                        "http", "chat.stream", "chat.cancelled",
                        message="请求已取消",
                        data={"child_id": child_id, "thread_id": thread_id},
                    )
                    await event_queue.put({
                        "type": "error",
                        "detail": "cancelled",
                        "retryable": False,
                        "status_code": None,
                        "error_type": "CancelledError",
                    })
        except asyncio.CancelledError:
            # 排队/获取信号量期间被取消：CancelledError 在 sem.acquire() 挂起点
            # 抛出，不经过内部取消分支（try 在其之后）。在此统一清理，避免
            # 残留 zombie 子会话 / task_bridge 映射，并归还排队计数。
            # [opencode abort 级联] 同时中断 supervisor 在途 handler（若已发出）。
            abort = getattr(agent_bus, "abort", None)
            if abort is not None:
                abort(thread_id)
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
                # B5: keep-alive 心跳 —— 必须真正 yield 字节给客户端。
                # 前端 multiAgent.ts STALL_TIMEOUT_MS=60s：任何 >60s 无 data 的
                # 真·安静期（长 tool_execute/子 Agent LLM 轮/等审批）都会触发前端
                # 断流 + 自动重试（看着像"任务循环"）。SSE 注释行（: 开头）不触发
                # 前端 onEvent，但能刷新 reader.read() 的 lastEventTime，维持连接。
                if event.get("type") == "_ping":
                    yield ": keep-alive\n\n"
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
                # [执行中追加任务] drain 时每个回合各有 collector，断连兜底要取
                # **当前回合**的（turn_ref 由 drain 循环逐轮更新），否则排队回合的
                # 部分结果会丢。
                agents = turn_ref["collector"].agents_snapshot()
                partial_answer = "\n\n".join(
                    a.get("content", "") for a in agents if a.get("content")
                )
                chainlog.warning(
                    "http", "chat.stream", "chat.client_disconnect",
                    message="SSE 未走到 done/error（客户端断开），兜底落库部分结果",
                    data={
                        "child_id": child_id,
                        "partial_answer_chars": len(partial_answer),
                        "agents": len(agents),
                    },
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


@router.post("/multi-agent/diff")
async def chat_multi_agent_diff(request: Request, body: DiffRequest):
    """[查看改动] 渲染某条 assistant 消息对应轮次的 diff 文本（聊天 UI 展开用）。

    body: {conversation_id, message_id, file?, step?}。file 为空返回本轮全部改动文件的
    diff 文本（受上限截断，truncated=true）；指定 file 只返回该文件。
    step=N 时只看第 N 步（每 step 快照）——「那一次写工具调用改了什么」。
    内部文件用 git diff <before_tree> <after_tree>；外部文件（工作区外/被 gitignore）
    用归档 blob 与当前内容做 difflib 对比。老消息没有 after_tree 时返回 reason 提示，
    不报错。
    """
    user_id = _get_user_id(request)
    try:
        service, session_id, _ = _resolve_multi_agent_parent(request, user_id, body.conversation_id, "")
    except session_repo.Forbidden:
        raise HTTPException(status_code=403, detail="Forbidden")
    except HTTPException:
        raise
    target = None
    try:
        for m in service.messages(user_id, session_id):
            if m.id == body.message_id and m.type == "assistant":
                target = m
                break
    except Exception:
        logger.exception("diff list messages failed: %s", session_id)
    if target is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    descriptor = (dict(target.data or {})).get("snapshot") or {}
    snap = getattr(request.app.state, "snapshot", None)
    try:
        return diff_turn(snap, descriptor, path=(body.file or ""), step=body.step)
    except Exception:
        logger.exception("diff render failed: %s", session_id)
        return {"files": [], "truncated": False, "reason": "diff 渲染失败"}


@router.post("/multi-agent/restore-snapshot")
async def chat_multi_agent_restore_snapshot(request: Request, body: RestoreSnapshotRequest):
    """[撤回改动] 恢复某条 assistant 消息对应轮次的文件改动。

    body: {conversation_id, message_id}。校验消息归属当前用户且为 assistant 类型，
    读取其 data.snapshot 恢复描述还原磁盘文件——内部（git worktree）文件从 before_tree
    恢复、外部文件写回归档内容（原来不存在则删除），并把该消息标记为已撤回
    （已撤回后再请求直接返回 already=True，避免把之后的人工修改再次覆盖掉）。
    任一文件失败只跳过该文件，不中断整体恢复。
    """
    user_id = _get_user_id(request)
    try:
        service, session_id, _ = _resolve_multi_agent_parent(request, user_id, body.conversation_id, "")
    except session_repo.Forbidden:
        raise HTTPException(status_code=403, detail="Forbidden")
    except HTTPException:
        raise
    target = None
    try:
        for m in service.messages(user_id, session_id):
            if m.id == body.message_id and m.type == "assistant":
                target = m
                break
    except Exception:
        logger.exception("restore-snapshot list messages failed: %s", session_id)
    if target is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    data = dict(target.data or {})
    if data.get("snapshot_restored"):
        return {
            "restored": True, "already": True,
            "internal": 0, "external": 0,
            "restored_files": data.get("snapshot_restored_files") or [],
        }
    descriptor = data.get("snapshot") or {}
    if not descriptor:
        raise HTTPException(status_code=400, detail="该消息没有可撤回的快照")
    snap = getattr(request.app.state, "snapshot", None)
    result = restore_session_turn(snap, descriptor)
    data["snapshot_restored"] = True
    data["snapshot_restored_files"] = result.get("restored") or []
    try:
        session_repo.update_message(session_id, body.message_id, data)
    except Exception:
        logger.exception("mark snapshot restored failed for %s", body.message_id)
    return {
        "restored": True, "already": False,
        "internal": result.get("internal", 0),
        "external": result.get("external", 0),
        "restored_files": result.get("restored") or [],
        "missing": result.get("missing") or [],
    }



__all__ = ["MAX_CONCURRENT_AGENTS", "MAX_QUEUE_SIZE", "_agent_semaphore", "_get_agent_semaphore", "_queue_counter", "chat_multi_agent", "chat_multi_agent_stream", "router"]
