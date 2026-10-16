"""平台 FastAPI 应用层（对齐 agent.md §9 app/）。

对外提供 graph 平台的 HTTP 接口：
- `/chat`、`/chat/stream`：对话（含 SSE 流式）
- `/sessions`：会话管理
- `/admin`：工具/提示词热更新与观测

启动：`uvicorn app.main:app --host 0.0.0.0 --port 8000`
"""

from .deps import PlatformContext, SessionStore, build_context, get_context

__all__ = ["PlatformContext", "SessionStore", "build_context", "get_context"]
