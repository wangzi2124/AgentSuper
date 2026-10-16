"""模型成本解析接口（对齐 agent.md §9 models 层）。

新核心（`observability.cost`）只依赖本模块，不依赖 `app.*`：
- 默认回退内建目录（`models.builtin_catalog`）的单价；
- 应用层（`app.models.catalog`）导入时通过 `register_cost_resolver` 注入产品目录
  权威解析器（含用户自定义模型 / override / provider 单价），优先级高于内建目录。

cost 单位：USD / 每 1M tokens。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from models.builtin_catalog import BUILTIN_CATALOG, ZERO_COST

_BY_ID: dict[str, dict[str, Any]] = {e["id"]: e for e in BUILTIN_CATALOG}

# 应用层注入的解析器（签名同 resolve_cost 的关键字参数）
_resolver: Optional[Callable[..., float]] = None


def register_cost_resolver(fn: Callable[..., float]) -> None:
    """注册应用层权威成本解析器（`app.models.catalog.resolve_cost`）。"""
    global _resolver
    _resolver = fn


def _builtin_cost(
    model_id: Optional[str],
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
) -> float:
    """基于内建目录的单价估算（未收录模型 → 0.0）。"""
    entry = _BY_ID.get(model_id or "") or {}
    cost = entry.get("cost") or ZERO_COST
    noncached = max(0, int(input_tokens) - int(cache_read))
    return (
        noncached * float(cost.get("input_per_1m", 0.0))
        + int(output_tokens) * float(cost.get("output_per_1m", 0.0))
        + int(cache_read) * float(cost.get("cache_read_per_1m", 0.0))
        + int(cache_write) * float(cost.get("cache_write_per_1m", 0.0))
    ) / 1_000_000


def resolve_cost(
    model_id: Optional[str],
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
) -> float:
    """返回一次 LLM 调用的美元成本；未知模型返回 0.0。

    优先应用层注入的解析器，失败或未注册则回退内建目录。
    """
    if _resolver is not None:
        try:
            return float(
                _resolver(
                    model_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read=cache_read,
                    cache_write=cache_write,
                )
                or 0.0
            )
        except Exception:  # noqa: BLE001 - 应用层异常时静默回退内建目录
            pass
    return _builtin_cost(
        model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
    )


__all__ = ["register_cost_resolver", "resolve_cost"]
