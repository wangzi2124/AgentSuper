"""Session 管理模块。

按 opencode 的 session 管理设计重构的骨架实现：
- db.py           SQLite 建表 / 连接（session.db，独立于旧 conversations.db）
- models.py       Session/Message/Part/Epoch/Input 的 Pydantic 模型
- repository.py   数据访问（归一化表 CRUD、级联删除、事件日志、输入队列）
- history.py      历史装载 + 上下文纪元（对齐 SessionHistory.load）
- coordinator.py  per-session 串行执行协调器（对齐 run-coordinator）
- service.py      业务门面（组合 repository+coordinator+history）
- deps.py         FastAPI 隔离依赖（User/Project/Session 三级）
- router.py       /api/sessions REST 路由
"""

from .db import init_db
from .deps import SessionContext
from .models import SessionInfo, SessionStatus
from .service import SessionService

__all__ = [
    "init_db",
    "SessionService",
    "SessionContext",
    "SessionInfo",
    "SessionStatus",
]
