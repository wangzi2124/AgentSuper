"""Model variants: ``provider/model#variant`` merge semantics (G-08)."""

from __future__ import annotations

from typing import Any


class UnknownVariant(ValueError):
    pass


def split_variant(model_id: str | None) -> tuple[str, str]:
    text = str(model_id or "").strip()
    if "#" not in text:
        return text, ""
    base, variant = text.split("#", 1)
    if not base.strip() or not variant.strip():
        raise UnknownVariant(f"invalid model variant: {model_id}")
    return base.strip(), variant.strip()


def _lower_headers(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key).lower(): str(item) for key, item in value.items()}


def find_variant(
    providers: dict[str, dict[str, Any]],
    model_entry: dict[str, Any] | None,
    base: str,
    variant: str,
) -> dict[str, Any]:
    provider_name = base.split("/", 1)[0] if "/" in base else ""
    provider = providers.get(provider_name) or {}
    candidates: list[Any] = []
    if isinstance(provider.get("variants"), dict):
        candidates.append(provider["variants"].get(variant))
    if isinstance(model_entry, dict) and isinstance(model_entry.get("variants"), dict):
        candidates.append(model_entry["variants"].get(variant))
    for item in candidates:
        if isinstance(item, dict):
            return item
    raise UnknownVariant(f"unknown variant: {variant}")


def apply_variant(
    base_result: dict[str, Any], variant_def: dict[str, Any], *, variant: str
) -> dict[str, Any]:
    result = dict(base_result)
    settings = variant_def.get("settings")
    if isinstance(settings, dict):
        options = dict(result.get("options") or {})
        options.update(settings)
        result["options"] = options
    headers = _lower_headers(variant_def.get("headers"))
    if headers:
        merged = _lower_headers(result.get("headers"))
        merged.update(headers)
        result["headers"] = merged
    body = variant_def.get("body")
    if isinstance(body, dict):
        merged_body = dict(result.get("body") or {})
        merged_body.update(body)
        result["body"] = merged_body
    if variant_def.get("temperature") is not None:
        result["temperature"] = variant_def["temperature"]
    result["variant"] = variant
    return result


def apply_overrides_to_kwargs(endpoint: dict[str, Any], kwargs: dict[str, Any]) -> None:
    """Map resolved headers/body onto LiteLLM kwargs."""
    headers = endpoint.get("headers")
    if isinstance(headers, dict) and headers:
        extra = dict(kwargs.get("extra_headers") or {})
        extra.update(_lower_headers(headers))
        kwargs["extra_headers"] = extra
    body = endpoint.get("body")
    if isinstance(body, dict) and body:
        extra_body = dict(kwargs.get("extra_body") or {})
        extra_body.update(body)
        kwargs["extra_body"] = extra_body


__all__ = [
    "UnknownVariant",
    "apply_overrides_to_kwargs",
    "apply_variant",
    "find_variant",
    "split_variant",
]
