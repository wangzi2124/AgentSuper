"""提示词工程（对齐 agent.md 第 7 章）。

分层组装：`[共享规则] + [模型 profile] + [Agent 角色]`；环境信息注入首条
HumanMessage，不污染 system prompt。提示词以文件管理，支持哈希追踪与 A/B（§7.1）。
"""

from prompts.loader import (
    DEFAULT_PROFILE,
    PROVIDER_PROFILE_MAP,
    PROMPTS_DIR,
    PromptLoader,
    build_agent_prompt,
    build_environment_message,
    model_id_of,
    profile_layer_enabled,
    prompt_hash,
    prompt_version,
    resolve_profile,
)

__all__ = [
    "DEFAULT_PROFILE",
    "PROVIDER_PROFILE_MAP",
    "PROMPTS_DIR",
    "PromptLoader",
    "build_agent_prompt",
    "build_environment_message",
    "model_id_of",
    "profile_layer_enabled",
    "prompt_hash",
    "prompt_version",
    "resolve_profile",
]
