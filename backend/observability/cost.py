"""成本估算（对齐 agent.md §11.3）。

优先用应用模型目录（`app.models.catalog`，产品权威单价，支持自定义模型 id），
再回退到 litellm 内置 cost map（需 provider 前缀），都不可用时返回 0.0。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _catalog_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """用模型层成本接口估算（USD）；未收录该模型时返回 0.0。

    应用层（`app.models.catalog`）导入时会注入产品目录权威单价；未注入则回退内建目录。
    """
    try:
        from models.pricing import resolve_cost

        return float(
            resolve_cost(
                model, input_tokens=prompt_tokens, output_tokens=completion_tokens
            )
            or 0.0
        )
    except Exception as e:  # noqa: BLE001 - 目录不可用（独立部署）时静默回退
        logger.debug("catalog cost unavailable for %s: %s", model, e)
        return 0.0


def _litellm_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """按 litellm 内置 cost map 估算（USD）；模型未映射时返回 0.0。"""
    try:
        from litellm import cost_per_token

        prompt_cost, completion_cost = cost_per_token(
            model=model,
            prompt_tokens=int(prompt_tokens),
            completion_tokens=int(completion_tokens),
        )
        return float(prompt_cost or 0.0) + float(completion_cost or 0.0)
    except Exception as e:  # noqa: BLE001
        logger.debug("litellm cost estimate unavailable for %s: %s", model, e)
        return 0.0


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """返回美元成本；未知模型返回 0.0。目录优先，litellm 兜底。"""
    if not model:
        return 0.0
    cost = _catalog_cost(model, prompt_tokens, completion_tokens)
    if cost:
        return cost
    return _litellm_cost(model, prompt_tokens, completion_tokens)
