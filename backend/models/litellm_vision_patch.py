"""litellm DeepSeek 兼容补丁（本地 monkeypatch）。

本模块包含两个相互独立的 DeepSeek 补丁：
  1. ``install_litellm_vision_patch``    —— 图像理解：含图消息保留 content 分块；
  2. ``install_litellm_reasoning_patch`` —— 思考模式：回填 assistant 的 reasoning_content。

一、图像理解
-----------

背景
----
DeepSeek 的 ``deepseek-flash``（旧名 ``deepseek-v4-flash``）自 2026 起支持
[图像理解](https://api-docs.deepseek.com/zh-cn/guides/vision)：OpenAI 兼容端点
接受 ``content`` 为分块数组，图片以 ``image_url`` 分块承载。

但 litellm 1.89.x 的 ``DeepSeekChatConfig._transform_messages`` 会**无条件**把
``content`` 列表扁平化成纯文本（``handle_messages_with_content_list_to_str_conversion``
→ ``convert_content_list_to_str`` 只拼接每个分块的 ``.text``），``image_url`` 分块被
静默丢弃。实测（本仓库 .env 的 deepseek key）：

- ``deepseek/deepseek-v4-flash``（走 litellm 的 deepseek provider）→ 模型收到不到图，
  content 为空、reasoning 里自称「当前没有提供图片」；
- 直连 ``openai`` SDK，或 ``openai/deepseek-v4-flash`` + ``api_base=deepseek``
  （走 litellm 的 openai provider）→ 正确识别图片。

另注意：litellm 本地 cost map 里 ``deepseek/*`` 没有 ``supports_vision``，且这个扁平化
是**无条件**的，所以给模型注册 ``supports_vision`` 也无效 —— 只能改这个转换本身。

补丁
----
仅当消息含图片分块时跳过扁平化，交回基类（OpenAI 兼容转换，能正确保留分块）；
纯文本消息行为与原来**完全一致**（仍走原扁平化）。幂等安装，失败只告警不阻断启动。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# OpenAI 兼容的多模态图片分块类型（chat: image_url；responses: input_image；anthropic: image）
_IMAGE_BLOCK_TYPES = ("image_url", "image", "input_image")


def _contains_image_block(messages) -> bool:
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") in _IMAGE_BLOCK_TYPES:
                return True
    return False


def install_litellm_vision_patch() -> bool:
    """安装 DeepSeek content-list 保图补丁；返回是否已生效（幂等）。"""
    try:
        from litellm.llms.deepseek.chat.transformation import DeepSeekChatConfig
    except Exception as e:  # noqa: BLE001 —— litellm 结构变化时静默跳过
        logger.warning("litellm DeepSeek vision patch skipped (import failed): %s", e)
        return False

    if getattr(DeepSeekChatConfig, "_agentsuper_vision_patched", False):
        return True

    _orig_transform = DeepSeekChatConfig._transform_messages

    def _transform_messages(self, messages, model, is_async=False):  # type: ignore[no-untyped-def]
        if _contains_image_block(messages):
            # 含图片：保留 content 分块，交给基类（OpenAI 兼容）转换
            base = super(DeepSeekChatConfig, self)
            if is_async:
                return base._transform_messages(messages=messages, model=model, is_async=True)
            return base._transform_messages(messages=messages, model=model, is_async=False)
        return _orig_transform(self, messages, model, is_async)

    DeepSeekChatConfig._transform_messages = _transform_messages  # type: ignore[assignment]
    DeepSeekChatConfig._agentsuper_vision_patched = True  # type: ignore[attr-defined]
    logger.info("litellm DeepSeek vision patch installed")
    return True


def install_litellm_reasoning_patch() -> bool:
    """让 litellm 对 DeepSeek 思考模式回填 ``reasoning_content``（修复多轮报错）。

    背景
    ----
    DeepSeek 思考模式要求：多轮对话中每个 ``assistant`` 消息都必须回传
    ``reasoning_content``，否则返回
    ``The reasoning_content in the thinking mode must be passed back to the API.``
    —— 实测**流式**请求（带 tools/历史）必现，非流式反而宽松。

    litellm 1.89.x 的 ``DeepSeekChatConfig._fill_reasoning_content`` 正是为此而写
    （从 ``provider_specific_fields`` 提升，或以单空格占位），但它被
    ``_thinking_mode_active`` 双重门控：

        supports_reasoning(model) AND optional_params["thinking"] == {"type": "enabled"}

    而 ``deepseek-v4-flash``/``deepseek-v4-pro`` 不在 litellm 本地 cost map 里，
    ``supports_reasoning()`` 返回 **False**，补丁因此永不生效。本仓库又只在注入
    ``tool_task`` 结果等场景触发（见 ``graph.generate`` 零进展救援），历史里带
    assistant 消息即命中。

    补丁
    ----
    只保留「显式 ``thinking={"type":"enabled"}``」这道门（由 ``catalog.provider_api``
    按模型的「推理模型」能力下发），去掉对 litellm 未知模型的 ``supports_reasoning``
    门控。幂等安装，失败只告警不阻断启动。
    """
    try:
        from litellm.llms.deepseek.chat.transformation import DeepSeekChatConfig
    except Exception as e:  # noqa: BLE001 —— litellm 结构变化时静默跳过
        logger.warning("litellm DeepSeek reasoning patch skipped (import failed): %s", e)
        return False

    if getattr(DeepSeekChatConfig, "_agentsuper_reasoning_patched", False):
        return True

    def _thinking_mode_active(self, model, optional_params):  # type: ignore[no-untyped-def]
        return (optional_params.get("thinking") or {}).get("type") == "enabled"

    DeepSeekChatConfig._thinking_mode_active = _thinking_mode_active  # type: ignore[assignment]
    DeepSeekChatConfig._agentsuper_reasoning_patched = True  # type: ignore[attr-defined]
    logger.info("litellm DeepSeek reasoning patch installed")
    return True
