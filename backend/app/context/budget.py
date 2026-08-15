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
    ratio = getattr(settings, "compaction_threshold_ratio", 0.65)
    return max(1, int(usable_context_tokens() * ratio))


def prune_protect_tokens() -> int:
    """prune 回溯清理的保护下限（对齐 opencode PRUNE_PROTECT）。

    关键修正：保护预算必须**低于压缩阈值**，否则 prune 在压缩之前永远没有
    机会工作（压缩总是在累积超过阈值时先触发）。配置值（默认 40_000）是为
    大上下文模型（200K+）设计的，本项目单次请求上下文在压缩阈值就触发
    压缩，因此取「压缩阈值的 1/2」作为有效保护预算（并尊重显式配置的较低值）。

    效果：工具输出累计超过阈值的一半时就开始回收更旧的输出，
    为后续轮次腾出空间，压缩触发前 prune 已经分担了大部分清理。
    """
    threshold = compaction_threshold_tokens()
    return max(1, min(settings.tool_output_protect_tokens, threshold // 2))


def prune_minimum_tokens() -> int:
    """prune 生效下限（对齐 opencode PRUNE_MINIMUM）。

    保护预算的 1/2 作为最低收益线（对齐 opencode 的 40K/20K 比例）：
    回收量达到该值才落桩，避免微小收益的频繁改写。
    """
    return max(1, prune_protect_tokens() // 2)
