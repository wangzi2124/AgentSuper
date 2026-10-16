"""执行层：虚拟文件系统 / 沙箱 / 代码执行 / 工具注册表（对齐 agent.md 第 5 章）。"""

from .code_runner import CodeRunner, run_code, supported_languages
from .filesystem import VirtualFileSystem
from .sandbox import BLOCKED_PATTERNS, shell_exec
from .tool_registry import SessionToolCache, ToolDefinition, ToolRegistry

__all__ = [
    "VirtualFileSystem",
    "BLOCKED_PATTERNS",
    "shell_exec",
    "CodeRunner",
    "run_code",
    "supported_languages",
    "ToolDefinition",
    "ToolRegistry",
    "SessionToolCache",
]
