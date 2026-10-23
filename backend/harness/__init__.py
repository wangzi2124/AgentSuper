"""跨模型工具调用 Harness（docs/agent-4.md）。

原生支持走 LiteLLM 归一化；不支持走提示词注入 + 定界符解析；两条路径统一输出
``tool_calls: [{name, arguments}]``，工具循环不感知厂商差异。
"""

from .adapter import (
    FALLBACK,
    NATIVE,
    apply_result,
    build_fallback_prompt,
    call_argument_error,
    extract_embedded_native_calls,
    inject_fallback_prompt,
)
from .capabilities import resolve_mode, supports_native_tools
from .delimiters import (
    ARGS_CLOSE,
    ARGS_OPEN,
    NAME_CLOSE,
    NAME_OPEN,
    HarnessToolCall,
    parse_tool_calls,
    render_tool_call,
    render_tool_result,
)
from .history import normalize_fallback, normalize_history, normalize_native
from .hooks import ToolHook, reset_tool_hooks, run_post_tool, run_pre_tool, set_tool_hooks
from .streaming import PreludeSuppressor, StreamToolCallParser
from .tool_adapter import (
    Done,
    HarnessEvent,
    TextDelta,
    ToolAdapter,
    ToolCallReady,
    get_tool_adapter,
)

__all__ = [
    "ARGS_CLOSE",
    "ARGS_OPEN",
    "Done",
    "FALLBACK",
    "HarnessEvent",
    "HarnessToolCall",
    "NAME_CLOSE",
    "NAME_OPEN",
    "NATIVE",
    "PreludeSuppressor",
    "StreamToolCallParser",
    "TextDelta",
    "ToolAdapter",
    "ToolCallReady",
    "ToolHook",
    "apply_result",
    "build_fallback_prompt",
    "call_argument_error",
    "extract_embedded_native_calls",
    "get_tool_adapter",
    "inject_fallback_prompt",
    "normalize_fallback",
    "normalize_history",
    "normalize_native",
    "parse_tool_calls",
    "render_tool_call",
    "render_tool_result",
    "reset_tool_hooks",
    "resolve_mode",
    "run_post_tool",
    "run_pre_tool",
    "set_tool_hooks",
    "supports_native_tools",
]
