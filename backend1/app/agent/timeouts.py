"""模型感知的超时倍数。

自托管 / 本地模型（Ollama，或 provider 的 api_base 指向本机）推理速度显著低于云端，
基础超时（按云端模型设定）会让长任务（多轮工具调用）被误判超时。这里统一判定"是否
自托管"并给出放大倍数，供 supervisor / sub-agent 的等待逻辑与 endpoint 使用。

设计要点：
- 只放大、不收窄（`max(1.0, factor)`），云端模型恒为 1.0，行为不变。
- 判定失败时退回保守值（不放大），避免因探测异常误放大超时。
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _host_of(api_base: Optional[str]) -> str:
    try:
        return (urlparse(api_base or "").hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def is_self_hosted_model(model_id: Optional[str]) -> bool:
    """该模型是否运行在本机（Ollama 或 api_base 指向 localhost）。"""
    mid = (model_id or "").strip()
    if not mid:
        return False
    if mid.startswith("ollama/"):
        return True
    try:
        from app.models.catalog import provider_api

        creds = provider_api(mid) or {}
        if creds.get("is_ollama"):
            return True
        return _host_of(creds.get("api_base")) in _LOCAL_HOSTS
    except Exception:  # noqa: BLE001
        # 探测失败：保守返回 False（不放大）
        return False


def model_timeout_factor(model_id: Optional[str]) -> float:
    """返回该模型的超时放大倍数（自托管=N，云端=1）。"""
    if not is_self_hosted_model(model_id):
        return 1.0
    try:
        from app.config import settings

        factor = float(getattr(settings, "local_model_timeout_factor", 6.0) or 6.0)
    except Exception:  # noqa: BLE001
        factor = 6.0
    return max(1.0, factor)


def scale_timeout(base: float, model_id: Optional[str]) -> float:
    """把基础超时按模型放大倍数缩放。"""
    return float(base) * model_timeout_factor(model_id)


# ── Agent 超时倍数（单一概念）─────────────────────────────────────────────
# 取代旧的「SUB_AGENT_TIMEOUT_EXTENDED + EXTENDED_TIMEOUT_AGENTS」双旋钮：
# 最终等待 = SUB_AGENT_TIMEOUT × multiplier[agent] × 模型自托管倍数。
# 默认 build（工具密集型主 Agent）为 2.0，其余 1.0。settings.agent_timeout_multipliers
# 可覆盖（`AGENT_TIMEOUT_MULTIPLIERS` 环境变量，JSON）。
DEFAULT_TIMEOUT_MULTIPLIERS: dict[str, float] = {"build": 2.0}


def timeout_multipliers() -> dict[str, float]:
    """当前生效的 Agent 超时倍数表（settings 覆盖 > 默认）。"""
    try:
        from app.config import settings

        raw = getattr(settings, "agent_timeout_multipliers", None)
    except Exception:  # noqa: BLE001
        raw = None
    if not raw:
        return dict(DEFAULT_TIMEOUT_MULTIPLIERS)
    try:
        items = raw.items()
    except AttributeError:
        return dict(DEFAULT_TIMEOUT_MULTIPLIERS)
    out: dict[str, float] = {}
    for key, value in items:
        try:
            out[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def agent_timeout_multiplier(agent_id: Optional[str]) -> float:
    """按 Agent 名称取超时倍数；未声明为 1.0。"""
    try:
        return max(0.0, float(timeout_multipliers().get(str(agent_id or ""), 1.0)))
    except (TypeError, ValueError):
        return 1.0


def agent_timeout(agent_id: Optional[str], model_id: Optional[str] = None) -> float:
    """子 Agent 等待超时 = 基础超时 × Agent 倍数 × 模型倍数（自托管放大）。"""
    try:
        from app.config import settings

        base = float(getattr(settings, "sub_agent_timeout", 150.0) or 150.0)
    except Exception:  # noqa: BLE001
        base = 150.0
    return scale_timeout(base * agent_timeout_multiplier(agent_id), model_id)
