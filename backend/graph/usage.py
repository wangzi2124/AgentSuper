"""Token / 成本用量归集（对齐 agent.md §11.3）。

图状态 `token_usage` 使用带 reducer 的字典累加各节点 LLM 用量：
- `merge_usage` 供 LangGraph 状态合并（数值键求和，其余覆盖）；
- `usage_from_result` 把单次 ChatResult 的 usage 归一化为统一口径（含 cost）；
- `usage_delta` 求一轮增量（跨轮 checkpoint 累计 → 本轮净用量）。

成本由 `observability.cost.estimate_cost` 估算（litellm 内置 cost map，未知模型 0）。
"""

from __future__ import annotations

from typing import Any

from observability.cost import estimate_cost

USAGE_NUMERIC_KEYS: tuple[str, ...] = (
    "input",
    "output",
    "total",
    "reasoning",
    "cache_read",
    "cache_write",
    "calls",
    "cost",
)


def empty_usage() -> dict[str, Any]:
    return {key: 0 for key in USAGE_NUMERIC_KEYS}


def merge_usage(
    old: dict[str, Any] | None, new: dict[str, Any] | None
) -> dict[str, Any]:
    """LangGraph reducer：数值键求和，其余键覆盖。"""
    merged = dict(old or {})
    for key, value in (new or {}).items():
        if key in USAGE_NUMERIC_KEYS and isinstance(value, (int, float)):
            merged[key] = merged.get(key, 0) + value
        else:
            merged[key] = value
    return merged


def usage_from_result(result: Any, model: str | None = None) -> dict[str, Any]:
    """把一次 ChatResult 的 usage 归一化为统一用量字典（含 cost）。"""
    raw = getattr(result, "usage", None) or {}
    if not isinstance(raw, dict):
        raw = {}
    prompt = int(raw.get("input", 0) or 0)
    completion = int(raw.get("output", 0) or 0)
    total = int(raw.get("total", 0) or 0) or (prompt + completion)
    # 成本按键优先用「配置的模型 id」（目录单价以此为键），退而用响应里的模型名
    model_name = str(model or getattr(result, "model", "") or "")
    cost = float(estimate_cost(model_name, prompt, completion)) if model_name else 0.0
    return {
        "input": prompt,
        "output": completion,
        "total": total,
        "reasoning": int(raw.get("reasoning", 0) or 0),
        "cache_read": int(raw.get("cache_read", 0) or 0),
        "cache_write": int(raw.get("cache_write", 0) or 0),
        "calls": 1,
        "cost": round(cost, 8),
    }


def usage_delta(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> dict[str, Any]:
    """本轮净用量（after - before）；跨轮 checkpoint 累计时用于取单轮增量。"""
    before = before or {}
    after = after or {}
    out: dict[str, Any] = {}
    for key in USAGE_NUMERIC_KEYS:
        diff = float(after.get(key, 0) or 0) - float(before.get(key, 0) or 0)
        out[key] = round(diff, 8) if key == "cost" else max(0, int(diff))
    return out
