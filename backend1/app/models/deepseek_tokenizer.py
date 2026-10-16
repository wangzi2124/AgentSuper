"""DeepSeek V4 官方 tokenizer 集成（费用/Token 统计口径）。

tokenizer 文件（LlamaTokenizerFast 的 tokenizer.json）位于：
`backend/data/models/deepseek-v4-tokenizer/`（也兼容环境变量 DEEPSEEK_TOKENIZER_DIR 覆盖）。

- 直接经 `tokenizers` 库加载 tokenizer.json，无需 transformers/sentencepiece。
- 文件缺失/加载失败时优雅降级：get_tokenizer() 返回 None，调用方回落
  token_counter 的 tiktoken/字符估算，绝不阻塞启动与在线调用。
- `count_messages_tokens` 按 tokenizer_config.json 的 chat_template 语义近似整段
  prompt（角色标记 + 工具调用 + 工具输出），作为“按这个 tokenizer 计 token/计费”
  的精确口径。
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── DeepSeek-V3/V4 对话模板标记（对齐 tokenizer_config.json chat_template）──
_DS_BOS = "<｜begin▁of▁sentence｜>"
_DS_USER = "<｜User｜>"
_DS_ASSIST = "<｜Assistant｜>"
_DS_EOS = "<｜end▁of▁sentence｜>"
_DS_TOOL_CALLS_BEGIN = "<｜tool▁calls▁begin｜>"
_DS_TOOL_CALL_BEGIN = "<｜tool▁call▁begin｜>"
_DS_TOOL_SEP = "<｜tool▁sep｜>"
_DS_TOOL_CALL_END = "<｜tool▁call▁end｜>"
_DS_TOOL_CALLS_END = "<｜tool▁calls▁end｜>"
_DS_TOOL_OUTPUTS_BEGIN = "<｜tool▁outputs▁begin｜>"
_DS_TOOL_OUTPUTS_END = "<｜tool▁outputs▁end｜>"
_DS_TOOL_OUTPUT_BEGIN = "<｜tool▁output▁begin｜>"
_DS_TOOL_OUTPUT_END = "<｜tool▁output▁end｜>"


_tokenizer: Optional[Any] = None
_tokenizer_ready = False
_lock = threading.Lock()


def _tokenizer_dir() -> Optional[Path]:
    """定位 tokenizer 目录：env 覆盖 → data/models → 仓库内置。"""
    env_dir = os.environ.get("DEEPSEEK_TOKENIZER_DIR")
    if env_dir:
        d = Path(env_dir)
        if (d / "tokenizer.json").exists():
            return d
    try:
        from app.storage.paths import global_paths
        d = Path(global_paths()["data"]) / "models" / "deepseek-v4-tokenizer"
        if (d / "tokenizer.json").exists():
            return d
    except Exception:
        pass
    try:
        repo = Path(__file__).resolve().parent / "deepseek-v4-tokenizer"
        if (repo / "tokenizer.json").exists():
            return repo
    except Exception:
        pass
    return None


def get_tokenizer() -> Optional[Any]:
    """惰性加载官方 tokenizer（线程安全；失败返回 None 且只警告一次）。"""
    global _tokenizer, _tokenizer_ready
    if _tokenizer_ready:
        return _tokenizer
    with _lock:
        if _tokenizer_ready:
            return _tokenizer
        _tokenizer_ready = True
        try:
            d = _tokenizer_dir()
            if d is None:
                raise FileNotFoundError("tokenizer.json not found")
            from tokenizers import Tokenizer
            _tokenizer = Tokenizer.from_file(str(d / "tokenizer.json"))
            logger.info("DeepSeek V4 native tokenizer loaded: %s", d)
        except Exception as e:  # noqa: BLE001
            logger.warning("DeepSeek V4 native tokenizer unavailable, falling back to estimates: %s", e)
            _tokenizer = None
        return _tokenizer


def tokenizer_available() -> bool:
    return get_tokenizer() is not None


def _flatten_list_content(content: Any) -> str:
    """OpenAI 风格 list content（多模态 text/image 块）→ 拼接文本。"""
    parts = []
    if isinstance(content, list):
        for p in content:
            if isinstance(p, dict):
                if p.get("type") == "text" and isinstance(p.get("text"), str):
                    parts.append(p["text"])
                elif p.get("type") == "image_url":
                    parts.append("[image]")
    return "\n".join(parts)


def _to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return _flatten_list_content(content)


def count_tokens(text: Any) -> int:
    """按官方 tokenizer 精确计数；tokenizer 不可用返回 0（调用方回落估算）。"""
    if text is None:
        return 0
    tok = get_tokenizer()
    if tok is None:
        return 0
    s = str(text)
    if not s:
        return 0
    try:
        return len(tok.encode(s).ids)
    except Exception:  # noqa: BLE001
        return 0


def _count_chunks(texts: list[str]) -> int:
    tok = get_tokenizer()
    if tok is None:
        return 0
    total = 0
    try:
        for t in texts:
            if t:
                total += len(tok.encode(t).ids)
    except Exception:  # noqa: BLE001
        return 0
    return total


def count_messages_tokens(messages: list[dict]) -> int:
    """按 DeepSeek chat_template 语义近似整段 prompt 的 token 数。

    覆盖角色标记、工具调用（<｜tool▁calls▁begin｜>…）、工具输出及末位生成提示。
    模板在 tokenizer_config.json 中逐字实现，这里是等价的 Python 近似；
    精度足以作为费用/token 统计口径（差值为模板边界 token，量级可忽略）。
    """
    if not messages:
        return 0
    tok = get_tokenizer()
    if tok is None:
        return 0

    system_parts = [m.get("content") for m in messages if m.get("role") == "system"]
    chunks: list[str] = []
    is_first_output = True
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            continue
        if role == "user":
            chunks.append(_DS_USER + _to_text(content or ""))
            continue
        if role == "assistant":
            tcs = m.get("tool_calls") or []
            if content is None and tcs:
                chunks.append(_DS_ASSIST + _DS_TOOL_CALLS_BEGIN)
                for tc in tcs:
                    fn = tc.get("function") or {}
                    args = fn.get("arguments", "")
                    if isinstance(args, (dict, list)):
                        import json as _json
                        args = _json.dumps(args, ensure_ascii=False)
                    chunks.append(
                        _DS_TOOL_CALL_BEGIN + "function" + _DS_TOOL_SEP
                        + str(fn.get("name", "")) + "\n```json\n" + str(args) + "\n```"
                        + _DS_TOOL_CALL_END
                    )
                chunks.append(_DS_TOOL_CALLS_END + _DS_EOS)
            elif content is not None:
                chunks.append(_DS_ASSIST + _to_text(content) + _DS_EOS)
            elif tcs:
                chunks.append(_DS_ASSIST + _DS_TOOL_CALLS_BEGIN + _DS_TOOL_CALLS_END + _DS_EOS)
            continue
        if role == "tool":
            marker = (
                _DS_TOOL_OUTPUTS_BEGIN + _DS_TOOL_OUTPUT_BEGIN
                if is_first_output
                else "\n" + _DS_TOOL_OUTPUT_BEGIN
            )
            is_first_output = False
            chunks.append(marker + _to_text(content or "") + _DS_TOOL_OUTPUT_END)
            continue

    if is_first_output:
        chunks.append(_DS_BOS + "\n\n".join(system_parts))
    else:
        chunks.append(_DS_TOOL_OUTPUTS_END)
    chunks.append(_DS_ASSIST)
    return _count_chunks(chunks)


def count_or_estimate(text: Any) -> int:
    """优先官方计数，失败回落字符估算（前端上下文条保证返回非零值）。"""
    n = count_tokens(text)
    if n > 0:
        return n
    s = str(text or "")
    if not s:
        return 0
    return max(1, round(len(s) / 4))