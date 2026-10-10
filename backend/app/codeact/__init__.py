"""CodeAct 受控执行器（设计 docs/codeact-sandbox-design.md，v2.1）。

Phase 1：无状态受控执行器 —— tool_code_exec 每次调用 spawn 一个新子进程，
六层防护（§4）：前置约束(§4.0) + AST 静态守卫(§4.1) + SAFE_BUILTINS/导入
白名单/audit hook(§4.2) + 路径校验(§4.3/§4.4) + 进程隔离 Job Object(§5) +
宿主 RPC 审批审计(§6/§7)。默认关闭（CODEACT_ENABLED=false）。
"""

from app.codeact.result import CodeActResult
from app.codeact.session import ExecutionSession

__all__ = ["CodeActResult", "ExecutionSession"]