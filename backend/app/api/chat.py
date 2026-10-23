"""对话接口（对齐 agent.md §9 app/api/chat.py）。

- `POST /chat`：非流式，跑完整个主图返回最终状态摘要。
- `POST /chat/stream`：SSE 流式，逐节点推送 `start`/`node`/`done` 事件。

响应契约（agent.md §10.4 / §10.8）：`output`（最终回答）、`thread_id`、
`resumed`（携带的 thread_id 已存在 → 续跑），并保留 `session_id`/`final_output`/`state`。
契约遵循 agent.md 简洁风格（直接 JSON，不套 {code,message,data} 信封）；
校验错误仍走全局统一信封（{code:422,...}）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..deps import ModelSwitchError, PlatformContext, get_context

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户输入")
    session_id: str | None = Field(default=None, description="会话 id")
    thread_id: str | None = Field(default=None, description="续跑线程 id")
    user_id: str = Field(default="anonymous")
    directory: str | None = Field(default=None, description="会话工作目录")
    target_model: str | None = Field(default=None, description="会话内切换模型")


@router.post("")
async def chat(body: ChatRequest, ctx: PlatformContext = Depends(get_context)) -> dict:
    tid = body.thread_id or body.session_id
    resumed = tid is not None and ctx.sessions.get(tid) is not None
    sess = ctx.sessions.ensure(
        tid, directory=body.directory, user_id=body.user_id
    )
    if body.target_model and body.target_model != sess.model:
        try:
            await ctx.switch_model(sess.id, body.target_model)
        except ModelSwitchError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    state = await ctx.run(body.message, session_id=sess.id, user_id=body.user_id)
    output = state.get("final_output", "")
    return {
        "output": output,
        "thread_id": sess.thread_id,
        "resumed": resumed,
        "session_id": sess.id,
        "final_output": output,
        "state": state,
    }


@router.post("/stream")
async def chat_stream(
    body: ChatRequest, ctx: PlatformContext = Depends(get_context)
) -> StreamingResponse:
    sess = ctx.sessions.ensure(
        body.session_id, directory=body.directory, user_id=body.user_id
    )
    if body.target_model and body.target_model != sess.model:
        try:
            await ctx.switch_model(sess.id, body.target_model)
        except ModelSwitchError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    async def event_source():
        async for ev in ctx.stream(body.message, session_id=sess.id, user_id=body.user_id):
            payload = json.dumps(ev, ensure_ascii=False, default=str)
            yield f"event: {ev['event']}\ndata: {payload}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
