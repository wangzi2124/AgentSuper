"""拆分模块 `persist`（含 _begin_task_session、_build_compressed_history、_ensure_child_pair、_existing_pair、_persist_interrupted_partial、_persist_multi_agent、_persist_multi_agent_parts、_resolve_multi_agent_parent、_session_history_for）。

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

from .helpers import _generate_title
from .helpers import _get_session_service
from .helpers import _get_summarizer
from .helpers import _msg_type_to_role
from .helpers import _sanitize_history
from .helpers import _truncate_history

logger = logging.getLogger(__name__)

# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──



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

async def _enrich_image_files(files: list | None) -> list:
    """[F8 · D2] 图片附件落库时附加缩略图 + 描述（回显/重放走缩略图，不重发原图）。

    - `_thumb`：256px 缩略图 base64（回显用，体积小）
    - `_caption`：图片描述（视觉 LLM/OCR，按指纹缓存；失败则为空，回显降级占位）
    """
    if not files:
        return files or []
    from app.agent.image_processor import describe_image, is_image_file, make_thumbnail
    enriched = []
    for f in files:
        f = dict(f or {})
        if is_image_file(f):
            try:
                f["_thumb"] = make_thumbnail(f.get("data") or "")
            except Exception:  # noqa: BLE001
                pass
            try:
                cap = await describe_image(
                    f.get("data") or "", f.get("mime_type") or "", f.get("filename") or ""
                )
                if cap:
                    f["_caption"] = cap
            except Exception:  # noqa: BLE001
                pass
        enriched.append(f)
    return enriched


def _session_history_for(service, user_id: str, session_id: str, limit: int = 200) -> list[dict]:
    """把会话消息投影为模型历史（role/content）。

    正文优先从 message_parts 的 text part 提取（对齐设计 §3），无 parts 时
    回退 data.content（旧数据/compaction/system 消息）。

    [F8 · D2] 用户消息带图片附件时，把已存的图片描述（_caption）注入历史文本，
    重放不重发原图（大 base64 只在首次请求携带），模型仍保留图片内容上下文。

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
            imgs = [
                f for f in (m.data.get("files") or [])
                if isinstance(f, dict) and (f.get("_caption") or f.get("_thumb"))
            ]
            if imgs:
                desc = "\n".join(
                    f"[图片 {f.get('filename', '')}]: {f.get('_caption') or '(缩略图已存档)'}"
                    for f in imgs
                )
                content = (content + "\n\n[用户上传的图片]\n" + desc).strip() if content else "[用户上传的图片]\n" + desc
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
                    "files": await _enrich_image_files(files) or [],
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



__all__ = ["_begin_task_session", "_build_compressed_history", "_ensure_child_pair", "_existing_pair", "_persist_interrupted_partial", "_persist_multi_agent", "_persist_multi_agent_parts", "_resolve_multi_agent_parent", "_session_history_for"]
