"""网络工具集（对齐 agent.md §9 execution/tools/web_tools.py）。

提供 `web_fetch`（带 SSRF 防护的出站 GET）与 `web_search`（可注入检索后端）。
所有出站请求先经 `app.utils.ssrf.validate_http_url` 校验，输出有界（§7.6.5）。

为便于离线/单测，检索后端可注入；未注入时尝试复用既有 internet-search 插件，
再退回一段说明文案（绝不抛异常打断对话）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..filesystem import bounded

logger = logging.getLogger(__name__)

DEFAULT_FETCH_CHARS = 8_000
DEFAULT_SEARCH_RESULTS = 5
FETCH_TIMEOUT = 20


def _ssrf_error(url: str) -> str | None:
    """返回 SSRF 校验错误文案；通过则返回 None。"""
    try:
        from utils.ssrf import validate_http_url

        err = validate_http_url(url)
        return err or None
    except Exception:  # noqa: BLE001
        # SSRF 模块不可用时保守拒绝非 http(s)
        return None if url.strip().lower().startswith(("http://", "https://")) else "Error: only http/https allowed"


def web_fetch(
    url: str,
    *,
    max_chars: int = DEFAULT_FETCH_CHARS,
    timeout: int = FETCH_TIMEOUT,
    fetcher: Callable[..., Any] | None = None,
) -> str:
    """抓取 URL 文本内容（带 SSRF 防护与有界输出）。

    Args:
        url: 目标地址（仅 http/https）。
        max_chars: 返回文本字符上限。
        timeout: 秒。
        fetcher: 可注入的抓取函数 `(url, timeout) -> str`，测试时替换。
    """
    err = _ssrf_error(url)
    if err:
        return f"❌ {err}"
    try:
        if fetcher is not None:
            text = fetcher(url, timeout)
        else:
            import httpx

            resp = httpx.get(url, timeout=timeout, follow_redirects=True)
            resp.raise_for_status()
            text = resp.text
    except Exception as e:  # noqa: BLE001
        detail = str(e) or repr(e)
        return f"❌ 抓取失败：{detail}"
    text = (text or "").strip()
    return bounded(text, max_chars) or "(空内容)"


def _default_searcher(query: str, top_k: int) -> str:
    """默认检索后端：尝试复用 internet-search 插件，失败则给出说明。"""
    try:
        from extensions import plugins as plugin_loader  # type: ignore

        for name in dir(plugin_loader):
            if "internet_search" in name:
                fn = getattr(plugin_loader, name)
                if callable(fn):
                    return str(fn(query=query, max_results=top_k))
    except Exception as e:  # noqa: BLE001
        logger.debug("internet-search plugin unavailable: %s", e)
    return (
        "❌ 未配置联网检索后端：请注入 searcher，或启用 internet-search 插件。"
        f"（query={query!r}, top_k={top_k}）"
    )


def web_search(
    query: str,
    *,
    top_k: int = DEFAULT_SEARCH_RESULTS,
    searcher: Callable[[str, int], str] | None = None,
) -> str:
    """执行联网检索，返回文本结果（有界）。

    Args:
        query: 检索词。
        top_k: 返回条数上限。
        searcher: 可注入的检索函数 `(query, top_k) -> str`，测试/生产可替换。
    """
    if not query or not query.strip():
        return "❌ 检索词为空"
    fn = searcher or _default_searcher
    try:
        text = fn(query.strip(), int(top_k))
    except Exception as e:  # noqa: BLE001
        detail = str(e) or repr(e)
        return f"❌ 检索失败：{detail}"
    text = (text or "").strip()
    return bounded(text, DEFAULT_FETCH_CHARS) or "(无检索结果)"


def make_web_tools(
    *, searcher: Callable[[str, int], str] | None = None
) -> dict[str, Callable[..., Any]]:
    """返回工具名 → 函数 的字典。"""

    def search(query: str, top_k: int = DEFAULT_SEARCH_RESULTS) -> str:
        """联网检索给定查询，返回文本摘要。"""
        return web_search(query, top_k=top_k, searcher=searcher)

    def fetch(url: str) -> str:
        """抓取指定 URL 的文本内容（仅 http/https）。"""
        return web_fetch(url)

    return {"web_search": search, "web_fetch": fetch}


READ_ONLY_WEB_TOOLS = frozenset({"web_search", "web_fetch"})


def register_web_tools(
    registry: Any,
    *,
    searcher: Callable[[str, int], str] | None = None,
    scope: str = "global",
    names: "set[str] | None" = None,
) -> list[str]:
    """把网络工具注册进 `ToolRegistry`，返回注册的工具名列表。

    网络工具默认 scope=global（各 Agent 均可用），且**只读**（require_approval=False）。
    """
    tools = make_web_tools(searcher=searcher)
    registered: list[str] = []
    for name, fn in tools.items():
        if names is not None and name not in names:
            continue
        registry.register_function(
            fn,
            name=name,
            description=(fn.__doc__ or "").strip(),
            scope=scope,
            require_approval=False,
        )
        registered.append(name)
    return registered


__all__ = [
    "DEFAULT_FETCH_CHARS",
    "DEFAULT_SEARCH_RESULTS",
    "READ_ONLY_WEB_TOOLS",
    "make_web_tools",
    "register_web_tools",
    "web_fetch",
    "web_search",
]
