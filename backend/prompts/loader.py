"""提示词加载器（对齐 agent.md §7.2 / §7.7）。

- 模板以文件管理，`Template.safe_substitute` 填充变量。
- `build_system_prompt` 组装 `Provider 前缀 + Agent 角色`。
- `prompt_hash` / `prompt_version` 支持哈希追踪与 Trace 归因。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).resolve().parent

_DOUBLE_BRACE = re.compile(r"\{\{(\w+)\}\}")

PROVIDER_BY_TIER = {
    "simple": "openai",
    "medium": "anthropic",
    "complex": "anthropic",
    "reasoning": "anthropic",
}
DEFAULT_PROVIDER = "anthropic"


class PromptLoader:
    """带缓存的提示词模板加载器。"""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else PROMPTS_DIR
        self._cache: dict[str, str] = {}

    def raw(self, template_path: str) -> str:
        if template_path not in self._cache:
            self._cache[template_path] = (
                self.base_dir / template_path
            ).read_text(encoding="utf-8")
        return self._cache[template_path]

    def load(self, template_path: str, **variables: object) -> str:
        # 同时兼容 `{{var}}`（Jinja 风格）与 `$var`（string.Template）两种占位符。
        normalized = _DOUBLE_BRACE.sub(r"$\1", self.raw(template_path))
        return Template(normalized).safe_substitute(**variables)

    def build_system_prompt(
        self, agent_type: str, provider: str = DEFAULT_PROVIDER
    ) -> str:
        parts = [
            self.load(f"providers/{provider}_prefix.md"),
            self.load(f"system/{agent_type}.md"),
        ]
        return "\n\n---\n\n".join(parts)

    @staticmethod
    def build_environment_message(working_dir: str, git_status: str = "") -> str:
        return (
            "<environment>\n"
            f"工作目录: {working_dir}\n"
            f"Git 状态: {git_status}\n"
            f"平台: {_platform()}\n"
            f"当前日期: {datetime.now().strftime('%Y-%m-%d')}\n"
            "</environment>"
        )


def _platform() -> str:
    import sys

    return {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "Linux")


def build_environment_message(working_dir: str, git_status: str = "") -> str:
    """模块级便捷函数（等价于 PromptLoader.build_environment_message）。"""
    return PromptLoader.build_environment_message(working_dir, git_status)


def build_for_tier(agent_type: str, tier: str) -> str:
    provider = PROVIDER_BY_TIER.get(tier, DEFAULT_PROVIDER)
    return PromptLoader().build_system_prompt(agent_type, provider)


def prompt_hash(agent_type: str, provider: str = DEFAULT_PROVIDER) -> str:
    """对组装后的 system prompt 取短哈希，用于 Trace 归因（§7.7.3）。"""
    text = PromptLoader().build_system_prompt(agent_type, provider)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def prompt_version(agent_type: str, provider: str = DEFAULT_PROVIDER) -> str:
    return f"{agent_type}.{provider}.{prompt_hash(agent_type, provider)}"
