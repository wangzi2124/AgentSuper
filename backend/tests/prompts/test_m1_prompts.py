"""L1 单元测试：提示词分层组装（agent.md §7）。"""

import pytest

from prompts.loader import (
    PromptLoader,
    build_agent_prompt,
    build_environment_message,
    model_id_of,
    prompt_hash,
    prompt_version,
    resolve_profile,
)


def test_build_agent_prompt_layers_shared_profile_and_role():
    text = PromptLoader().build_agent_prompt("supervisor", "anthropic/claude-sonnet")
    assert "# 全局规则（最高优先级）" in text
    assert "# Claude 系列行为准则" in text
    assert "Supervisor" in text
    # 顺序：shared → profile → role
    assert text.index("全局规则") < text.index("Claude 系列行为准则") < text.index("Supervisor")


def test_provider_profile_mapping():
    assert resolve_profile("anthropic/claude-sonnet") == "anthropic"
    assert resolve_profile("openai/gpt-4o") == "gpt"
    assert resolve_profile("google/gemini-2.5-pro") == "gemini"
    assert resolve_profile("gemini/gemini-2.5-pro") == "gemini"
    assert resolve_profile("kimi/kimi-k2") == "kimi"
    assert resolve_profile("moonshot/moonshot-v1") == "kimi"
    assert resolve_profile("trinity/trinity-mini") == "trinity"
    assert resolve_profile("deepseek/deepseek-v4-pro") == "default"
    assert resolve_profile("ollama_chat/qwen2.5:7b") == "default"
    assert resolve_profile("ollama/qwen2.5:7b") == "default"
    assert resolve_profile("") == "default"
    assert resolve_profile(None) == "default"


def test_model_id_of_supports_method_and_attribute():
    class WithMethod:
        def model_name(self) -> str:
            return "openai/gpt-4o"

    class WithAttribute:
        model_name = "openai/gpt-4o"

    class Missing:
        pass

    assert model_id_of(WithMethod()) == "openai/gpt-4o"
    assert model_id_of(WithAttribute()) == "openai/gpt-4o"
    assert model_id_of(Missing()) is None
    assert model_id_of(None) is None


def test_build_agent_prompt_uses_default_for_unknown_provider():
    text = build_agent_prompt("code_agent", "deepseek/deepseek-v4-pro")
    assert "# 通用模型行为准则" in text
    assert "# GPT 系列行为准则" not in text


def test_profile_layer_can_be_disabled(monkeypatch):
    monkeypatch.setenv("PROMPT_PROFILE_LAYER", "0")
    text = PromptLoader().build_agent_prompt("supervisor", "openai/gpt-4o")
    assert "全局规则" not in text
    assert "GPT 系列行为准则" not in text
    assert "Supervisor" in text


def test_environment_message_injected_as_block():
    msg = build_environment_message(working_dir="/tmp/ws", git_status="clean")
    assert "<environment>" in msg
    assert "/tmp/ws" in msg
    assert "Git 状态: clean" in msg


def test_prompt_hash_stable_and_short():
    h1 = prompt_hash("supervisor")
    h2 = prompt_hash("supervisor")
    assert h1 == h2 and len(h1) == 12
    assert prompt_version("supervisor", "default").endswith(h1)


def test_prompt_hash_differs_by_profile():
    assert prompt_hash("supervisor", "default") != prompt_hash("supervisor", "gpt")


def test_template_substitution():
    text = PromptLoader().load(
        "system/worker_base.md",
        AGENT_NAME="测试 Agent",
        AGENT_ROLE="测试角色",
    )
    assert "测试 Agent" in text


def test_missing_system_prompt_raises():
    with pytest.raises(FileNotFoundError):
        build_agent_prompt("no_such_agent", "openai/gpt-4o")
