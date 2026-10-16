"""L1 单元测试：提示词分层组装（agent.md §7）。"""

from prompts.loader import (
    PromptLoader,
    build_environment_message,
    build_for_tier,
    prompt_hash,
)


def test_build_system_prompt_layers_provider_and_role():
    text = PromptLoader().build_system_prompt("supervisor", "anthropic")
    assert "provider_notes" in text
    assert "主协调 Agent" in text


def test_build_for_tier_maps_provider():
    # simple→openai，medium/complex→anthropic
    assert "Markdown 标题" in build_for_tier("code_agent", "simple")
    assert "XML 标签" in build_for_tier("code_agent", "complex")


def test_environment_message_injected_as_block():
    msg = build_environment_message(working_dir="/tmp/ws", git_status="clean")
    assert "<environment>" in msg
    assert "/tmp/ws" in msg
    assert "Git 状态: clean" in msg


def test_prompt_hash_stable_and_short():
    h1 = prompt_hash("supervisor")
    h2 = prompt_hash("supervisor")
    assert h1 == h2 and len(h1) == 12


def test_template_substitution():
    text = PromptLoader().load(
        "system/worker_base.md",
        AGENT_NAME="测试 Agent",
        AGENT_ROLE="测试角色",
    )
    assert "测试 Agent" in text
