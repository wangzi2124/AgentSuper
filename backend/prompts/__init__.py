"""提示词工程（对齐 agent.md 第 7 章）。

分层组装：`[Provider 前缀] + [Agent 角色]`；环境信息注入首条 HumanMessage，
不污染 system prompt。提示词以文件管理，支持哈希追踪与 A/B（§7.1）。
"""

from prompts.loader import (
    PROMPTS_DIR,
    PromptLoader,
    build_environment_message,
    build_for_tier,
    prompt_hash,
    prompt_version,
)

__all__ = [
    "PROMPTS_DIR",
    "PromptLoader",
    "build_environment_message",
    "build_for_tier",
    "prompt_hash",
    "prompt_version",
]
