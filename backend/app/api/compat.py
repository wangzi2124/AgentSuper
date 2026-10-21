"""前端兼容层（对齐 `frontend/src/api/multiAgent.ts` 的 SSE 协议）。

- `POST /api/chat/multi-agent/stream` → SSE，事件为 `data: {json}`，JSON 带 `type` 字段；
  响应头注入 `X-Session-Id`；终态事件 `done` / `error`。
- `POST /api/chat/multi-agent` → `{code,message,data}` 信封（前端 `apiRequest` 会解包）。
- `POST /api/chat/multi-agent/restore-snapshot` → 撤回某轮文件改动（快照）。
- `POST /api/chat/multi-agent/diff` → 渲染某轮 diff 文本。

把平台主图（supervisor → plan/rag/execute/synthesize）的节点执行映射成前端能渲染的
事件：`routing` → `agent_start`/`agent_step`/`agent_done` → `done`（携带 answer /
conversation_id / title / agents 快照 / tokens / cost）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from graph.nodes.base import reset_permission_hook, set_permission_hook
from graph.usage import merge_usage, usage_delta
from models.router import reset_request_model, set_request_model

from execution.filesystem import reset_allowed_roots, set_allowed_roots

from ..deps import PlatformContext, get_context, initial_state
from ..skills.slash import apply_slash_skill
from ..image_processor import enrich_image_files

router = APIRouter(prefix="/chat", tags=["compat"])

logger = logging.getLogger(__name__)

# SSE 心跳间隔（秒）：`stream_mode="updates"` 只在节点完成时产出 chunk，长节点
# （如 execute_subagent 的 ≤8 轮工具循环，实测可 60-90s）期间零事件 → 前端 60s
# stall 看门狗掐流并误报「网络连接中断」。任何 `data:` 行都会刷新前端
# lastEventTime，故以远小于 60s 的间隔发心跳保持连接（前端对未知 type 静默忽略）。
_HEARTBEAT_SEC = 12

# 平台图节点 → 前端 agent 标识/展示名
_NODE_AGENT = {
    "filter": ("build", "build"),
    "plan": ("plan", "plan"),
    "rag": ("build", "build"),
    "memory_recall": ("build", "build"),
    "context_compress": ("build", "build"),
    "execute_subagent": ("build", "build"),
    "parallel": ("build", "build"),
    "synthesize": ("build", "build"),
    "memory_store": ("build", "build"),
}


class MultiAgentRequest(BaseModel):
    message: str = Field(..., min_length=1)
    conversation_id: str | None = None
    model: str | None = None
    use_vector_db: bool = False
    directory: str | None = None
    client_msg_id: str | None = None
    agent_mode: str | None = None
    files: list[dict] | None = None
    voice: dict | None = None


class RestoreSnapshotRequest(BaseModel):
    conversation_id: str | None = None
    message_id: str = Field(..., min_length=1)


class DiffRequest(BaseModel):
    conversation_id: str | None = None
    message_id: str = Field(..., min_length=1)
    file: str | None = None
    step: int | None = None


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def _summarize(update: object) -> dict:
    """节点更新摘要（避免把整段 messages 回灌给前端）。"""
    if not isinstance(update, dict):
        return {"update": str(update)[:200]}
    out: dict = {"keys": [k for k in update.keys() if k != "messages"]}
    for k in ("final_output", "current_task_id", "draft_output", "iteration_count"):
        if k in update:
            out[k] = update[k]
    return out


def _agents_snapshot(seen: list[str]) -> list[dict]:
    return [{"agent_id": a, "agent_name": a, "status": "done"} for a in seen]


_AGENT_EVENT_TYPES = {"agent_start", "agent_step", "agent_done", "agent_error"}


def _track_agent_event(agents: dict[str, dict[str, Any]], ev: dict[str, Any]) -> None:
    """把流式 `agent_*` 事件累积成可持久化的 agents 快照（刷新后回放仍见工具步骤）。

    形状对齐前端 `AgentStreamData`（`stores/multiAgent.ts` 的 `agentsMap`）：按 `step_id`
    就地更新步骤，`done`/`error` 更新状态 —— 结果随 assistant 消息写进检查点的
    `additional_kwargs`（见 `_append_assistant` / `deps.session_messages`）。
    """
    etype = ev.get("type")
    agent_id = ev.get("agent_id")
    if etype not in _AGENT_EVENT_TYPES or not agent_id:
        return
    agent = agents.get(agent_id)
    if agent is None:
        agent = {
            "agent_id": agent_id,
            "agent_name": ev.get("agent_name") or agent_id,
            "agent_avatar": ev.get("agent_avatar") or "",
            "status": "running",
            "content": "",
            "steps": [],
        }
        agents[agent_id] = agent
    if ev.get("agent_name"):
        agent["agent_name"] = ev["agent_name"]
    if ev.get("agent_avatar"):
        agent["agent_avatar"] = ev["agent_avatar"]
    if ev.get("type") == "agent_step":
        step = ev.get("step") or {}
        sid = step.get("step_id")
        if not sid:
            return
        for i, old in enumerate(agent["steps"]):
            if old.get("step_id") == sid:
                agent["steps"][i] = {**old, **step}
                return
        agent["steps"].append(step)
    elif ev.get("type") == "agent_done":
        agent["status"] = "completed"
        if ev.get("content"):
            agent["content"] = ev["content"]
    elif ev.get("type") == "agent_error":
        agent["status"] = "failed"
        if ev.get("error"):
            agent["error"] = ev["error"]


def _turn_meta(
    agents_map: dict[str, dict[str, Any]], files_changed: list[dict[str, Any]]
) -> dict[str, Any]:
    """本轮 UI 细节（随 assistant 消息持久化，刷新/重开后历史回放可见）。"""
    return {"agents": list(agents_map.values()), "files_changed": files_changed}


async def _discard(event: dict) -> None:  # 非流式路径：审批事件无需下发
    return None


def _enter_roots(ctx: PlatformContext, session_id: str | None):
    """登记本回合允许的额外根（会话目录 + 「工作目录」面板），返回 contextvar token。

    覆盖图执行与轮次快照（snapshot/restore/diff 也 `_resolve` 同一批外部路径）。
    """
    return set_allowed_roots(ctx.allowed_roots_for(session_id or ""))


async def _append_assistant(ctx: PlatformContext, session_id: str, text: str, ui: dict | None = None) -> None:
    """把本轮正文追加进 thread checkpoint（供历史回放：`/sessions/{id}/messages`）。

    将 `ui`（agents/steps/files_changed 等前端细节）写入 `AIMessage.additional_kwargs["ui"]`
    以便 `deps.session_messages` 在历史回放时原样透传给前端。
    """
    if not text:
        return
    from langchain_core.messages import AIMessage

    msg = AIMessage(content=text)
    if ui:
        try:
            msg.additional_kwargs = dict(getattr(msg, "additional_kwargs", {}) or {})
            msg.additional_kwargs["ui"] = ui
        except Exception:  # noqa: BLE001
            pass
    await ctx.graph.aupdate_state(
        {"configurable": {"thread_id": session_id}}, {"messages": [msg]}
    )


async def _graph_events(
    ctx: PlatformContext,
    sess: Any,
    state: dict,
    seen: list[str],
    cancel: asyncio.Event,
    agents_map: dict[str, dict[str, Any]],
):
    """迭代主图 `astream`，并把高危工具审批（`permission_request`）与图事件交错下发。

    审批事件由 `ctx.request_permission` 经 `emit_perm` 推入本地队列；本生成器用
    `asyncio.wait` 同时等待「图下一块」与「审批事件」，从而在工具循环阻塞等待用户
    决策时仍能把 `permission_request` 送到前端（对齐真实审批流）。
    """
    perm_q: asyncio.Queue = asyncio.Queue()

    async def emit_perm(event: dict) -> None:
        await perm_q.put(event)

    async def hook(tool: str, args: dict) -> bool:
        return await ctx.request_permission(
            session_id=sess.id, tool=tool, args=args, emit=emit_perm
        )

    token = set_permission_hook(hook)
    # 本回合选中的模型（模型管理页）随 contextvar 传播到图节点任务；此前 body.model
    # 只写进会话元数据、不参与调用（改 key/换模型都不生效的根因）。
    model_token = set_request_model(getattr(sess, "model", None))
    agen = ctx.graph.astream(
        state, config={"configurable": {"thread_id": sess.id}}, stream_mode=["updates", "messages"]
    ).__aiter__()
    next_task = asyncio.ensure_future(agen.__anext__())
    try:
        while True:
            if cancel.is_set():
                break
            get_task = asyncio.ensure_future(perm_q.get())
            done, _ = await asyncio.wait(
                {next_task, get_task},
                return_when=asyncio.FIRST_COMPLETED,
                timeout=_HEARTBEAT_SEC,
            )
            if not done:
                # 心跳：图仍在执行但没有节点完成（长工具循环），继续等下一块。
                # next_task 保持挂起，get_task 重建（与既有每轮重建语义一致）。
                yield {"type": "heartbeat", "ts": round(time.time(), 3)}
                get_task.cancel()
                continue
            if get_task in done:
                yield get_task.result()
            elif not get_task.done():
                get_task.cancel()
            if next_task in done:
                try:
                    chunk = next_task.result()
                except StopAsyncIteration:
                    break
                if isinstance(chunk, tuple) and len(chunk) == 2:
                    mode, payload = chunk
                    if mode == "messages":
                        msg_chunk, meta = payload
                        content = getattr(msg_chunk, "content", None)
                        if content:
                            if isinstance(content, str) and content:
                                yield {"type": "text_delta", "agent_id": "", "delta": content}
                            elif isinstance(content, list):
                                for part in content:
                                    if isinstance(part, str) and part:
                                        yield {"type": "text_delta", "agent_id": "", "delta": part}
                                    elif isinstance(part, dict):
                                        text = part.get("text") or part.get("content")
                                        if isinstance(text, str) and text:
                                            yield {"type": "text_delta", "agent_id": "", "delta": text}
                        next_task = asyncio.ensure_future(agen.__anext__())
                        continue
                    chunk = payload if isinstance(payload, dict) else {}
                for node, update in (chunk or {}).items():
                    # [面板整合] supervisor 是编排节点，其步骤与 plan/build 重复，
                    # 且从不发 agent_done（旧逻辑跳过）→ 面板永久「运行中」。直接不作为
                    # 独立 Agent 面板下发，只保留真正干活的 plan/build（各自会收到 agent_done）。
                    if node == "supervisor":
                        continue
                    agent_id, agent_name = _NODE_AGENT.get(node, (node, node))
                    if agent_id not in seen:
                        seen.append(agent_id)
                        yield {
                            "type": "agent_start",
                            "agent_id": agent_id,
                            "agent_name": agent_name,
                            "agent_avatar": "",
                        }
                    summary = json.dumps(_summarize(update), ensure_ascii=False, default=str)[:500]
                    step_ev = {
                        "type": "agent_step",
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "step": {
                            "type": "step_end",
                            "step_id": f"{node}-{len(seen)}",
                            "name": node,
                            "status": "completed",
                            "detail": summary,
                        },
                    }
                    _track_agent_event(agents_map, step_ev)
                    yield step_ev
                    done_ev = {
                        "type": "agent_done",
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                    }
                    _track_agent_event(agents_map, done_ev)
                    yield done_ev
                next_task = asyncio.ensure_future(agen.__anext__())
    finally:
        if not next_task.done():
            next_task.cancel()
        reset_permission_hook(token)
        reset_request_model(model_token)


async def _current_usage(ctx: PlatformContext, session_id: str) -> dict:
    """读取当前会话累计 token_usage（无 checkpoint 时返回空字典）。"""
    try:
        snap = await ctx.state(session_id)
    except Exception:  # noqa: BLE001 - 空 thread / checkpointer 未就绪
        return {}
    usage = snap.get("token_usage") if isinstance(snap, dict) else None
    return usage if isinstance(usage, dict) else {}


def _token_fields(usage: dict) -> dict:
    """前端 `tokens` 口径（input/output/reasoning/cache_read/cache_write）。"""
    return {
        "input": int(usage.get("input", 0) or 0),
        "output": int(usage.get("output", 0) or 0),
        "reasoning": int(usage.get("reasoning", 0) or 0),
        "cache_read": int(usage.get("cache_read", 0) or 0),
        "cache_write": int(usage.get("cache_write", 0) or 0),
    }


def _accumulate_session_usage(
    ctx: PlatformContext, session_id: str, usage: dict
) -> None:
    """把本轮净用量累加进会话聚合列（sessions.tokens_* / cost）。"""
    sess = ctx.sessions.get(session_id)
    if sess is None:
        return
    tokens = merge_usage(sess.tokens or {}, usage)
    cost = float(sess.cost or 0.0) + float(usage.get("cost", 0.0) or 0.0)
    ctx.sessions.update(session_id, tokens=tokens, cost=round(cost, 8))


@router.post("/multi-agent/stream")
async def multi_agent_stream(
    body: MultiAgentRequest,
    ctx: PlatformContext = Depends(get_context),
    x_user_id: str | None = Header(default=None),
) -> StreamingResponse:
    user_id = x_user_id or "anonymous"
    sess = ctx.sessions.ensure(
        body.conversation_id, directory=body.directory, user_id=user_id, model=body.model
    )
    if not sess.title or sess.title == "新会话":
        ctx.sessions.update(sess.id, title=body.message[:20])

    user_msg_id = f"um_{uuid4().hex[:12]}"

    async def event_source():
        from ..snapshot import snapshot_begin, snapshot_end

        # [D3] 字段名与前端 store 对齐：`model_ref`（{id, name, provider}）。
        if sess.model:
            yield _sse(
                {
                    "type": "model_switched",
                    "model_ref": {"id": sess.model, "name": sess.model, "provider": ""},
                }
            )

        cancel = ctx.begin_turn(sess.id)
        roots_token = _enter_roots(ctx, sess.id)
        slot_queued: int | None = None
        sem = ctx.semaphore
        try:
            if sem is not None and sem.locked():
                if ctx.queue_counter >= ctx.max_queue_size:
                    yield _sse(
                        {
                            "type": "error",
                            "error": "服务繁忙，排队已满，请稍后重试",
                            "detail": "服务繁忙，排队已满，请稍后重试",
                            "retryable": True,
                            "status_code": 429,
                            "error_type": "QueueFullError",
                        }
                    )
                    return
                ctx.queue_counter += 1
                slot_queued = ctx.queue_counter
                yield _sse({"type": "queued", "queue_position": slot_queued})

            async with sem:
                if slot_queued is not None:
                    ctx.queue_counter = max(0, ctx.queue_counter - 1)
                    slot_queued = None

                turn_question = body.message
                turn_user_msg_id = user_msg_id
                turn_files = await enrich_image_files(body.files)
                while True:
                    # 队列快照仅提示前端「本轮之后还有活」；done/turn_done 由末尾原子 pop 决定
                    pending = len(sess.prompts)
                    if pending:
                        yield _sse(
                            {
                                "type": "queue_drain",
                                "queue_size": pending,
                                "detail": f"本轮完成后继续执行 {pending} 个排队任务",
                            }
                        )
                    yield _sse(
                        {"type": "routing", "agent_id": "supervisor", "detail": "正在分析并路由..."}
                    )
                    ctx.sessions.update(sess.id, status="running")
                    seen: list[str] = []
                    agents_map: dict[str, dict[str, Any]] = {}
                    snap_begin = snapshot_begin(ctx.vfs)
                    before_usage = await _current_usage(ctx, sess.id)
                    state = initial_state(
                        # `/技能名`：本轮强制加载技能正文（找不到/未启用则原样发送）
                        apply_slash_skill(turn_question, getattr(ctx, "skill_loader", None))[0],
                        user_id,
                        files=turn_files if turn_user_msg_id == user_msg_id else None,
                        model=sess.model,
                        use_vector_db=bool(body.use_vector_db),
                    )
                    async for ev in _graph_events(ctx, sess, state, seen, cancel, agents_map):
                        yield _sse(ev)

                    snapshot = await ctx.state(sess.id)
                    final = snapshot.get("final_output", "") or ""
                    after_usage = snapshot.get("token_usage") or {}
                    usage = usage_delta(before_usage, after_usage)
                    files_changed, descriptor = snapshot_end(ctx.vfs, snap_begin)
                    assistant_msg_id = f"am_{uuid4().hex[:12]}"
                    ctx.snapshots[assistant_msg_id] = descriptor
                    tokens = _token_fields(usage)
                    cost = float(usage.get("cost", 0.0) or 0.0)
                    _accumulate_session_usage(ctx, sess.id, usage)

                    # 原子认领下一个排队任务（后来居上）→ 决定 done 还是 turn_done。
                    # [停止即终态] 用户在回合中按了停止：不再认领排队任务，直接收尾为
                    # done —— 否则会发 turn_done 后 break、缺一个终态 done，前端读到干净
                    # 断流却因未收到终态事件而误判为 network 错误（横幅卡「运行中」/误触发重试）。
                    if cancel.is_set():
                        nxt = None
                    else:
                        nxt = sess.prompts.pop() if sess.prompts else None
                    is_final = nxt is None
                    ctx.sessions.update(sess.id, status="idle", title=sess.title)

                    agents_map_snapshot = dict(agents_map)
                    await _append_assistant(
                        ctx,
                        sess.id,
                        final,
                        ui=_turn_meta(agents_map_snapshot, files_changed),
                    )

                    # 权威完整正文流式下发（前端 text_delta 逐段渲染，done 再兜底覆盖）
                    if final:
                        yield _sse({"type": "text_delta", "agent_id": "", "delta": final})
                    yield _sse(
                        {
                            "type": "done" if is_final else "turn_done",
                            "agent_id": "",
                            "answer": final,
                            "conversation_id": sess.id,
                            "title": sess.title,
                            "model": sess.model,
                            "agents": list(agents_map_snapshot.values()) or _agents_snapshot(seen),
                            "tokens": tokens,
                            "cost": round(cost, 8),
                            "files_changed": files_changed,
                            "plan_path": None,
                            "user_msg_id": turn_user_msg_id,
                            "assistant_msg_id": assistant_msg_id,
                            "queued_turn": not is_final,
                            "next_prompt_id": nxt.get("id") if nxt else None,
                        }
                    )
                    if is_final or cancel.is_set():
                        break
                    turn_question = str(nxt.get("message") or "")
                    turn_files = nxt.get("files") or None
                    turn_user_msg_id = f"um_{uuid4().hex[:12]}"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转 error 事件
            ctx.sessions.update(sess.id, status="error")
            yield _sse(
                {
                    "type": "error",
                    "agent_id": "",
                    "error": f"{type(exc).__name__}: {exc}",
                    "detail": str(exc),
                    "retryable": False,
                    "status_code": None,
                    "error_type": type(exc).__name__,
                }
            )
        finally:
            if slot_queued is not None:
                ctx.queue_counter = max(0, ctx.queue_counter - 1)
            ctx.end_turn(sess.id)
            reset_allowed_roots(roots_token)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Session-Id": sess.id},
    )


@router.post("/multi-agent")
async def multi_agent(
    body: MultiAgentRequest,
    ctx: PlatformContext = Depends(get_context),
    x_user_id: str | None = Header(default=None),
) -> dict:
    user_id = x_user_id or "anonymous"
    sess = ctx.sessions.ensure(
        body.conversation_id, directory=body.directory, user_id=user_id, model=body.model
    )
    if not sess.title or sess.title == "新会话":
        ctx.sessions.update(sess.id, title=body.message[:20])
    before_usage = await _current_usage(ctx, sess.id)
    roots_token = _enter_roots(ctx, sess.id)
    try:
        from ..snapshot import snapshot_begin, snapshot_end

        snap_begin = snapshot_begin(ctx.vfs)

        async def _hook(tool: str, args: dict) -> bool:
            return await ctx.request_permission(
                session_id=sess.id, tool=tool, args=args, emit=_discard
            )

        token = set_permission_hook(_hook)
        try:
            state = await ctx.run(
                body.message,
                session_id=sess.id,
                user_id=user_id,
                files=await enrich_image_files(body.files),
                use_vector_db=bool(body.use_vector_db),
            )
        finally:
            reset_permission_hook(token)
        files_changed, descriptor = snapshot_end(ctx.vfs, snap_begin)
        assistant_msg_id = f"am_{uuid4().hex[:12]}"
        ctx.snapshots[assistant_msg_id] = descriptor
        await _append_assistant(ctx, sess.id, state.get("final_output", "") or "", ui={"files_changed": files_changed})
    except Exception as exc:  # noqa: BLE001
        return {
            "code": 500,
            "message": f"{type(exc).__name__}: {exc}",
            "data": None,
            "detail": str(exc),
        }
    finally:
        reset_allowed_roots(roots_token)
    usage = usage_delta(before_usage, state.get("token_usage") or {})
    _accumulate_session_usage(ctx, sess.id, usage)
    ctx.sessions.update(sess.id, status="idle", title=sess.title)
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "answer": state.get("final_output", ""),
            "conversation_id": sess.id,
            "title": sess.title,
            "sources": [],
            "steps": [],
            "routed_to": "build",
            "tokens": _token_fields(usage),
            "cost": round(float(usage.get("cost", 0.0) or 0.0), 8),
            "files_changed": files_changed,
            "plan_path": None,
            "assistant_msg_id": assistant_msg_id,
        },
    }


@router.post("/multi-agent/restore-snapshot")
async def restore_snapshot(
    body: RestoreSnapshotRequest,
    ctx: PlatformContext = Depends(get_context),
    x_user_id: str | None = Header(default=None),
) -> dict:
    """撤回某轮文件改动：把该轮 assistant 消息对应的快照还原到磁盘。"""
    from ..snapshot import restore

    descriptor = ctx.snapshots.get(body.message_id)
    if not descriptor:
        return {
            "restored": False,
            "already": True,
            "internal": 0,
            "external": 0,
            "restored_files": [],
            "missing": [],
        }
    roots_token = _enter_roots(ctx, body.conversation_id)
    try:
        return restore(ctx.vfs, descriptor)
    finally:
        reset_allowed_roots(roots_token)


@router.post("/multi-agent/diff")
async def multi_agent_diff(
    body: DiffRequest,
    ctx: PlatformContext = Depends(get_context),
    x_user_id: str | None = Header(default=None),
) -> dict:
    """渲染某轮 diff 文本（聊天卡片展开用）。"""
    from ..snapshot import render_diff

    descriptor = ctx.snapshots.get(body.message_id)
    if not descriptor:
        return {"files": [], "truncated": False, "reason": "无该轮快照", "steps": 0}
    roots_token = _enter_roots(ctx, body.conversation_id)
    try:
        return render_diff(ctx.vfs, descriptor, file=body.file or None)
    finally:
        reset_allowed_roots(roots_token)
