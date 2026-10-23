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
