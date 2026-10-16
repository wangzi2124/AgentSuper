"""瀵硅瘽鎺ュ彛锛堝榻?agent.md 搂9 app/api/chat.py锛夈€?
- `POST /chat`锛氶潪娴佸紡锛岃窇瀹屾暣涓富鍥捐繑鍥炴渶缁堢姸鎬佹憳瑕併€?- `POST /chat/stream`锛歋SE 娴佸紡锛岄€愯妭鐐规帹閫?`start`/`node`/`done` 浜嬩欢銆?
濂戠害閬靛惊 agent.md 绠€娲侀鏍硷紙鐩存帴 JSON锛屼笉濂?{code,message,data} 淇″皝锛夛紱
鍚庣画濡傞渶瀵规帴鏃㈡湁 Vue 鍓嶇锛屽彲鍦ㄦ鍔犱竴灞傞€傞厤銆?"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..deps import PlatformContext, get_context

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="鐢ㄦ埛杈撳叆")
    session_id: str | None = Field(default=None, description="浼氳瘽 id锛涚己鐪佸垯鏂板缓")
    user_id: str = Field(default="anonymous")
    directory: str | None = Field(default=None, description="浼氳瘽宸ヤ綔鐩綍")


@router.post("")
async def chat(body: ChatRequest, ctx: PlatformContext = Depends(get_context)) -> dict:
    sess = ctx.sessions.ensure(
        body.session_id, directory=body.directory, user_id=body.user_id
    )
    state = await ctx.run(body.message, session_id=sess.id, user_id=body.user_id)
    return {
        "session_id": sess.id,
        "final_output": state.get("final_output", ""),
        "state": state,
    }


@router.post("/stream")
async def chat_stream(
    body: ChatRequest, ctx: PlatformContext = Depends(get_context)
) -> StreamingResponse:
    sess = ctx.sessions.ensure(
        body.session_id, directory=body.directory, user_id=body.user_id
    )

    async def event_source():
        async for ev in ctx.stream(body.message, session_id=sess.id, user_id=body.user_id):
            payload = json.dumps(ev, ensure_ascii=False, default=str)
            yield f"event: {ev['event']}\ndata: {payload}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
