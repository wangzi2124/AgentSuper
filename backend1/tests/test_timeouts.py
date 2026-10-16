# -*- coding: utf-8 -*-
"""app/agent/timeouts.py 用例：自托管模型判定、超时放大倍数、supervisor 分级超时联动。

[慢模型自适应超时 2026-10-11] 本地/自托管模型（Ollama 等）每轮 LLM 往返 60–120s，基础
超时按云端模型设定会让长任务被误判超时。这里验证：
- is_self_hosted_model：ollama 前缀 / provider is_ollama / api_base 本机主机；
- model_timeout_factor / scale_timeout：自托管放大、云端不变；
- SupervisorAgent._timeout_for：按 agent 分级 + 模型倍数叠加。

运行：pytest tests/test_timeouts.py
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import app.agent.timeouts as T


# ── is_self_hosted_model ───────────────────────────────────────────────────

def test_ollama_prefix_is_self_hosted():
    assert T.is_self_hosted_model("ollama/qwen2.5-coder:latest") is True
    assert T.is_self_hosted_model("ollama/qwen3.5:4b") is True


def test_cloud_model_not_self_hosted(monkeypatch):
    monkeypatch.setattr("app.models.catalog.provider_api",
                        lambda m: {"api_base": "https://api.deepseek.com", "is_ollama": False})
    assert T.is_self_hosted_model("deepseek/deepseek-v4-flash") is False


def test_provider_is_ollama_flag(monkeypatch):
    monkeypatch.setattr("app.models.catalog.provider_api",
                        lambda m: {"api_base": "http://remote:11434", "is_ollama": True})
    assert T.is_self_hosted_model("some/model") is True


def test_localhost_api_base_is_self_hosted(monkeypatch):
    for base in ("http://localhost:11434/v1", "http://127.0.0.1:8080", "http://0.0.0.0:1234"):
        monkeypatch.setattr("app.models.catalog.provider_api",
                            lambda m, b=base: {"api_base": b, "is_ollama": False})
        assert T.is_self_hosted_model("m/x") is True, base


def test_empty_and_none_not_self_hosted():
    assert T.is_self_hosted_model("") is False
    assert T.is_self_hosted_model(None) is False


def test_probe_failure_conservative(monkeypatch):
    def boom(m):
        raise RuntimeError("no catalog")
    monkeypatch.setattr("app.models.catalog.provider_api", boom)
    assert T.is_self_hosted_model("deepseek/deepseek-v4-flash") is False


# ── model_timeout_factor / scale_timeout ───────────────────────────────────

def test_factor_cloud_is_one(monkeypatch):
    monkeypatch.setattr(T, "is_self_hosted_model", lambda m: False)
    assert T.model_timeout_factor("deepseek/deepseek-v4-flash") == 1.0


def test_factor_self_hosted_uses_setting(monkeypatch):
    monkeypatch.setattr(T, "is_self_hosted_model", lambda m: True)
    monkeypatch.setattr("app.config.settings.local_model_timeout_factor", 6.0)
    assert T.model_timeout_factor("ollama/x") == 6.0


def test_factor_never_below_one(monkeypatch):
    monkeypatch.setattr(T, "is_self_hosted_model", lambda m: True)
    monkeypatch.setattr("app.config.settings.local_model_timeout_factor", 0.1)
    assert T.model_timeout_factor("ollama/x") == 1.0


def test_scale_timeout(monkeypatch):
    monkeypatch.setattr(T, "is_self_hosted_model", lambda m: m.startswith("ollama/"))
    monkeypatch.setattr("app.config.settings.local_model_timeout_factor", 6.0)
    assert T.scale_timeout(300, "ollama/qwen2.5-coder:latest") == 1800.0
    assert T.scale_timeout(300, "deepseek/deepseek-v4-flash") == 300.0


# ── agent_timeout_multiplier / agent_timeout（单一倍数表）───────────────────

def test_agent_timeout_multiplier_default_and_missing(monkeypatch):
    monkeypatch.setattr("app.config.settings.agent_timeout_multipliers", {"build": 2.0})
    assert T.agent_timeout_multiplier("build") == 2.0
    assert T.agent_timeout_multiplier("plan") == 1.0
    assert T.agent_timeout_multiplier(None) == 1.0


def test_timeout_multipliers_falls_back_to_default(monkeypatch):
    # 空配置 → 内置默认（build ×2），不返回空表
    monkeypatch.setattr("app.config.settings.agent_timeout_multipliers", {})
    assert T.timeout_multipliers() == T.DEFAULT_TIMEOUT_MULTIPLIERS


def test_agent_timeout_combines_base_and_factor(monkeypatch):
    monkeypatch.setattr(T, "is_self_hosted_model", lambda m: m == "ollama/x")
    monkeypatch.setattr("app.config.settings.local_model_timeout_factor", 6.0)
    monkeypatch.setattr("app.config.settings.sub_agent_timeout", 150.0)
    monkeypatch.setattr("app.config.settings.agent_timeout_multipliers", {"build": 2.0})
    assert T.agent_timeout("build") == 300.0
    assert T.agent_timeout("build", "ollama/x") == 1800.0
    assert T.agent_timeout("plan") == 150.0
    assert T.agent_timeout("unknown", "ollama/x") == 900.0
