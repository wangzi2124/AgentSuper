"""模型适配层（Model Adapter）。

对齐桌面 `agent.md` 第 2 章：所有 LLM 调用统一经 `get_model()` 获取，
按 tier（simple / medium / complex / reasoning）路由，生产环境走 LiteLLM Proxy，
开发环境（未设置 `LITELLM_PROXY_URL`）回退为直连 litellm。
"""

from .dialect import (
    extract_tool_call_obj,
    parse_streamed_tool_call,
    sanitize_tool_call_content,
    tcc_prefix_ok,
)
from .router import (
    FINISH_REASON_MAP,
    TIER_MAP,
    TIER_ORDER,
    ChatResult,
    TierModel,
    ToolCall,
    _ensure_provider,
    embed,
    ensure_provider,
    get_model,
    normalize_finish_reason,
)
from .stream import StreamResult, collect_stream, nonstream_result, stream_chat

__all__ = [
    "FINISH_REASON_MAP",
    "TIER_MAP",
    "TIER_ORDER",
    "ChatResult",
    "StreamResult",
    "TierModel",
    "ToolCall",
    "collect_stream",
    "embed",
    "_ensure_provider",
    "ensure_provider",
    "extract_tool_call_obj",
    "get_model",
    "normalize_finish_reason",
    "nonstream_result",
    "parse_streamed_tool_call",
    "sanitize_tool_call_content",
    "stream_chat",
    "tcc_prefix_ok",
]
