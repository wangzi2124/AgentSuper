"""提示词加载器（对齐 agent.md §7.2 / §7.7）。

- 模板以文件管理，`Template.safe_substitute` 填充变量。
- `build_agent_prompt` 按模型 provider 组装 `共享规则 + 模型 profile + Agent 角色`。
- `PROMPT_PROFILE_LAYER=0` 可关闭模型 profile 层，回退为纯角色提示词。
- `prompt_hash` / `prompt_version` 支持哈希追踪与 Trace 归因。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from string import Template

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent
_FILE_CACHE: dict[str, tuple[float, str]] = {}
_FILE_LOCK = threading.Lock()

_DOUBLE_BRACE = re.compile(r"\{\{(\w+)\}\}")

# catalog provider → prompt profile。只按 provider 区分，不猜模型家族。
PROVIDER_PROFILE_MAP = {
    "anthropic": "anthropic",
    "openai": "gpt",
    "google": "gemini",
    "gemini": "gemini",
    "kimi": "kimi",
    "moonshot": "kimi",
    "trinity": "trinity",
}
DEFAULT_PROFILE = "default"
SHARED_PROMPT = "providers/_shared.md"

_PROFILE_LAYER_ENV = "PROMPT_PROFILE_LAYER"
_FALSE_VALUES = {"0", "false", "off", "no"}


def profile_layer_enabled() -> bool:
    """模型 profile 层开关；默认开启，`PROMPT_PROFILE_LAYER=0` 关闭。"""
    raw = os.environ.get(_PROFILE_LAYER_ENV, "1").strip().lower()
    return raw not in _FALSE_VALUES


def model_id_of(model: object) -> str | None:
    """从模型对象提取完整 id（兼容 `TierModel.model_name()` 方法与普通属性）。"""
    value = getattr(model, "model_name", None)
    if callable(value):
        try:
            value = value()
        except Exception:  # noqa: BLE001 - 解析失败按未知模型处理
            return None
    return value if isinstance(value, str) and value else None


def resolve_profile(model_id: str | None) -> str:
    """由完整模型 id（`provider/model`）解析 profile；未知 provider 落 default。"""
    provider = (model_id or "").split("/", 1)[0].strip().lower()
    if provider == "ollama_chat":
        provider = "ollama"
    return PROVIDER_PROFILE_MAP.get(provider, DEFAULT_PROFILE)


class PromptLoader:
    """带缓存的提示词模板加载器。"""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else PROMPTS_DIR
        self._cache: dict[str, str] = {}

    def raw(self, template_path: str) -> str:
        path = self.base_dir / template_path
        key = str(path.resolve())
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        with _FILE_LOCK:
            cached = _FILE_CACHE.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        text = path.read_text(encoding="utf-8")
        with _FILE_LOCK:
            _FILE_CACHE[key] = (mtime, text)
        self._cache[template_path] = text
        return text

    def load(self, template_path: str, **variables: object) -> str:
        # 同时兼容 `{{var}}`（Jinja 风格）与 `$var`（string.Template）两种占位符。
        normalized = _DOUBLE_BRACE.sub(r"$\1", self.raw(template_path))
        return Template(normalized).safe_substitute(**variables)

    def build_agent_prompt(self, agent_type: str, model_id: str | None = None) -> str:
        """组装某个 Agent 的 system prompt，模型 profile 按 provider 选择。"""
        if not profile_layer_enabled():
            return self.load(f"system/{agent_type}.md")
        profile = resolve_profile(model_id)
        logger.debug(
            "prompt profile: agent=%s model=%s profile=%s", agent_type, model_id, profile
        )
        return self._compose(agent_type, profile)

    def _compose(self, agent_type: str, profile: str) -> str:
        parts = [
            self.load(SHARED_PROMPT),
            self.load(f"providers/{profile}.md"),
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


def build_agent_prompt(agent_type: str, model_id: str | None = None) -> str:
    """模块级便捷函数（等价于 PromptLoader.build_agent_prompt）。"""
    return PromptLoader().build_agent_prompt(agent_type, model_id)


def prompt_hash(agent_type: str, profile: str = DEFAULT_PROFILE) -> str:
    """对组装后的 system prompt 取短哈希，用于 Trace 归因（§7.7.3）。"""
    loader = PromptLoader()
    if profile_layer_enabled():
        text = loader._compose(agent_type, profile)
    else:
        text = loader.load(f"system/{agent_type}.md")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def prompt_version(agent_type: str, profile: str = DEFAULT_PROFILE) -> str:
    return f"{agent_type}.{profile}.{prompt_hash(agent_type, profile)}"
