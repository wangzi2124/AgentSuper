"""平台 API 路由集合（对齐 agent.md §9 app/api/ + 前端兼容层）。"""

from . import admin, chat, compat, sessions

__all__ = ["admin", "chat", "compat", "sessions"]
