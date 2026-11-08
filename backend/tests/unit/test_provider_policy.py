"""G-06: provider.use policy blocks model calls before transport."""

from __future__ import annotations

import pytest

from config import settings
from models.runtime import ModelRuntime, RuntimeSpec


def test_provider_policy_blocks_before_transport(monkeypatch):
    old = settings.policies
    monkeypatch.setattr(
        settings,
        "policies",
        [{"action": "provider.use", "resource": "deepseek/*", "effect": "deny"}],
    )
    try:
        rt = ModelRuntime(RuntimeSpec(model_override="deepseek/deepseek-v4-flash"))
        with pytest.raises(PermissionError, match="provider"):
            rt.complete([{"role": "user", "content": "hi"}])
    finally:
        settings.policies = old
