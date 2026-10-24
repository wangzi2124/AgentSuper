"""模型工具调用能力探测（agent-4 §3 / §6-P0）。

策略：未知模型一律走文本回退（保守），显式能力表优先于目录探测。
可用环境变量人工覆盖：

- ``HARNESS_TOOL_MODE=native|fallback``：全局强制模式（排障用）
- ``HARNESS_NATIVE_MODELS=a,b``：额外声明原生支持
- ``HARNESS_FALLBACK_MODELS=a,b``：额外声明不支持原生
"""

from __future__ import annotations

import os

NATIVE = "native"
FALLBACK = "fallback"
MODES = (NATIVE, FALLBACK)


def _split_env(name: str) -> set[str]:
    raw = os.environ.get(name, "") or ""
    return {part.strip() for part in raw.split(",") if part.strip()}


def _model_candidates(model_id: str | None) -> list[str]:
    mid = (model_id or "").strip()
    if not mid:
        return []
    out = [mid]
    for prefix in ("ollama_chat/", "ollama/"):
        if mid.startswith(prefix) and mid[len(prefix):]:
            out.append(mid[len(prefix):])
    return out


def mode_override(model_id: str | None) -> str | None:
    """人工覆盖：全局强制 > 回退白名单 > 原生白名单。"""
    force = (os.environ.get("HARNESS_TOOL_MODE") or "").strip().lower()
    if force in MODES:
        return force
    candidates = _model_candidates(model_id)
    fallback = _split_env("HARNESS_FALLBACK_MODELS")
    native = _split_env("HARNESS_NATIVE_MODELS")
    if any(c in fallback for c in candidates):
        return FALLBACK
    if any(c in native for c in candidates):
        return NATIVE
    return None


def resolve_mode(
    model_id: str | None, capabilities: dict[str, bool] | None = None
) -> str:
    """解析本模型应走的工具调用路径。

    ``capabilities`` 为 ``None`` 时从模型目录读取；读不到（未知模型）→ 回退。
    """
    override = mode_override(model_id)
    if override:
        return override
    caps = capabilities
    if caps is None:
        try:
            from models.catalog import read_capabilities

            caps = read_capabilities(model_id)
        except Exception:  # noqa: BLE001 - 目录不可用时保守回退
            caps = {}
    if not caps:
        return FALLBACK
    return NATIVE if caps.get("tool_use") else FALLBACK


def supports_native_tools(
    model_id: str | None, capabilities: dict[str, bool] | None = None
) -> bool:
    return resolve_mode(model_id, capabilities) == NATIVE


_PROFILE_TTL = 60.0
_profile_cache: dict[str, tuple[float, "ModelProfileSnapshot"]] = {}


def clear_profile_cache() -> None:
    _profile_cache.clear()


def build_profile(
    model_id: str | None,
    *,
    capabilities: dict[str, bool] | None = None,
    limits: dict[str, int] | None = None,
    force: bool = False,
) -> "ModelProfileSnapshot":
    """Build the per-request model profile (catalog + env override + TTL)."""
    import time

    from .contracts import ModelProfileSnapshot

    mid = (model_id or "").strip()
    now = time.time()
    cached = _profile_cache.get(mid)
    if cached and not force and now - cached[0] < _PROFILE_TTL:
        return cached[1]

    caps = capabilities
    if caps is None:
        try:
            from models.catalog import read_capabilities

            caps = read_capabilities(mid)
        except Exception:  # noqa: BLE001
            caps = {}
    lims = limits
    if lims is None:
        try:
            from models.catalog import read_limits

            lims = read_limits(mid)
        except Exception:  # noqa: BLE001
            lims = {}

    mode = resolve_mode(mid, caps)
    source = "env" if mode_override(mid) else ("catalog" if caps else "default")
    profile = ModelProfileSnapshot(
        model_id=mid,
        tool_mode=mode,
        context_length=int((lims or {}).get("context_length") or 0),
        max_output_tokens=int((lims or {}).get("max_output_tokens") or 0),
        vision=bool((caps or {}).get("vision")),
        reasoning=bool((caps or {}).get("reasoning")),
        source=source,
    )
    _profile_cache[mid] = (now, profile)
    return profile


__all__ = [
    "build_profile",
    "clear_profile_cache",
    "mode_override",
    "resolve_mode",
    "supports_native_tools",
]
