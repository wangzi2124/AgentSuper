"""可观测层（对齐 agent.md 第 11 章 / §1.2）。

- tracing：LangFuse 接入（未安装时降级为本地 trace 记录，不阻断）。
- metrics：Token / 成本计量。
- cost：成本归因。
"""

from .cost import estimate_cost
from .metrics import MetricsRecorder, get_recorder, record_llm_call
from .tracing import TraceSpan, get_trace_handler, trace_span

__all__ = [
    "estimate_cost",
    "MetricsRecorder",
    "get_recorder",
    "record_llm_call",
    "TraceSpan",
    "get_trace_handler",
    "trace_span",
]
