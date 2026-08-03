"""Token 预算计算模块。

对齐 opencode `packages/opencode/src/session/overflow.ts`：
    usable = max(0, min(context, input) - reserve)
    reserve = min(20_000, maxOutputTokens)

AgentSuper 用 `max_context_tokens` 表示上下文上限、`context_reserve_tokens`
表示输出预留，二者相减即为可用的上下文预算；压缩阈值默认取其 80%，
保证长工具循环在触顶截断（兜底）之前先触发压缩（总结而非丢弃）。
"""

from app.config import settings


def usable_context_tokens() -> int:
    """单次 LLM 调用可用的上下文预算（system + history + 当前轮 + 工具记录）。

    已扣掉输出预留，因此调用方在 truncate 时不应再二次预留。
    """
    return max(0, settings.max_context_tokens - settings.context_reserve_tokens)


def compaction_threshold_tokens() -> int:
    """触发上下文压缩的 token 阈值。

    显式配置 >0 时用配置值；否则取 usable 的 80%，在截断兜底之前先压缩。
    """
    if settings.compaction_threshold_tokens > 0:
        return settings.compaction_threshold_tokens
    return max(1, int(usable_context_tokens() * 0.8))
