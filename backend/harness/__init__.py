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
    stable_call_id,
)
from .budget import BudgetExceeded, BudgetLedger
from .capabilities import resolve_mode, supports_native_tools
from .compression import (
    CompressionError,
    CompressionPlan,
    CompressionResult,
    compress_messages,
    summarize_messages,
    tail_snapshot,
)
from .context import assemble_model_view
from .contracts import EventEnvelope, ModelProfileSnapshot, TurnContext, TurnResult
from .errors import (
    AuthError,
    BudgetExceededError,
    ContextOverflowError,
    HarnessError,
    RateLimitError,
    UpstreamError,
    classify_error,
)
from .kernel import (
    TurnHarness,
    as_turn_result,
    current_turn,
    reset_current_turn,
    set_current_turn,
)
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
from .switch import ModelSwitchError, SwitchDeps, SwitchService
from .tool_history import (
    cap_tool_history,
    make_tool_call_message,
    make_tool_result_message,
    merge_tool_history,
)
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
    "AuthError",
    "BudgetExceeded",
    "BudgetExceededError",
    "BudgetLedger",
    "CompressionError",
    "CompressionPlan",
    "CompressionResult",
    "ContextOverflowError",
    "Done",
    "EventEnvelope",
    "FALLBACK",
    "HarnessError",
    "HarnessEvent",
    "HarnessToolCall",
    "ModelProfileSnapshot",
    "ModelSwitchError",
    "NAME_CLOSE",
    "NAME_OPEN",
    "NATIVE",
    "PreludeSuppressor",
    "RateLimitError",
    "StreamToolCallParser",
    "SwitchDeps",
    "SwitchService",
    "TextDelta",
    "ToolAdapter",
    "ToolCallReady",
    "ToolHook",
    "TurnContext",
    "TurnHarness",
    "TurnResult",
    "UpstreamError",
    "apply_result",
    "assemble_model_view",
    "as_turn_result",
    "build_fallback_prompt",
    "call_argument_error",
    "cap_tool_history",
    "classify_error",
    "compress_messages",
    "current_turn",
    "reset_current_turn",
    "extract_embedded_native_calls",
    "get_tool_adapter",
    "inject_fallback_prompt",
    "make_tool_call_message",
    "make_tool_result_message",
    "merge_tool_history",
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
    "set_current_turn",
    "set_tool_hooks",
    "stable_call_id",
    "summarize_messages",
    "tail_snapshot",
    "supports_native_tools",
]
