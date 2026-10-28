"""内置网络工具（对齐 opencode 的 webfetch / websearch 语义）。

提供：
- `web_fetch`：只读 GET，抓取 URL 文本（SSRF 防护 + 有界输出）。
- `web_search`：联网检索（Tavily / Bing / DuckDuckGo，按环境变量择优）。
- `http_request`：通用 HTTP 请求（GET/POST/PUT/DELETE/PATCH/HEAD/OPTIONS），
  经 `utils.ssrf` 校验；注册时 `require_approval=True`（每次人工审批）。

所有出站请求先经 SSRF 校验，避免访问内网/环回/云元数据地址。
"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from ..filesystem import bounded

logger = logging.getLogger(__name__)

DEFAULT_FETCH_CHARS = 8_000
DEFAULT_SEARCH_RESULTS = 5
FETCH_TIMEOUT = 20
HTTP_MAX_RESPONSE_CHARS = 5_000
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


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


def _parse_headers(headers: str) -> dict[str, str]:
    if not headers:
        return {}
    try:
        parsed = json.loads(headers)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid headers JSON — {headers} ({e})") from e
    if not isinstance(parsed, dict):
        raise ValueError("headers must be a JSON object")
    return {str(k): str(v) for k, v in parsed.items()}


def http_request(
    method: str,
    url: str,
    headers: str = "",
    body: str = "",
    content_type: str = "application/json",
    timeout: int = 30,
) -> str:
    """Send an HTTP request to any endpoint (testing APIs, webhooks, fetching data).

    Parameters:
    - method: HTTP method (GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS)
    - url: Target URL (http/https only; internal addresses are blocked)
    - headers: Request headers as a JSON object string, e.g. {"Authorization": "Bearer xxx"}
    - body: Request body (JSON string for JSON, or key=value&... for form data)
    - content_type: Content-Type header (default application/json)
    - timeout: Request timeout in seconds (default 30)
    """
    m = (method or "GET").upper().strip()
    allowed = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")
    if m not in allowed:
        return f"Error: Invalid HTTP method '{m}'. Must be one of: {', '.join(allowed)}"

    err = _ssrf_error(url)
    if err:
        return f"Error: {err}"

    try:
        req_headers = {"User-Agent": "AgentSuper/1.0 (built-in http_request)"}
        req_headers.update(_parse_headers(headers))
    except ValueError as e:
        return f"Error: {e}"

    data: bytes | None = None
    if body and m not in ("GET", "HEAD", "OPTIONS"):
        if "json" in (content_type or "").lower():
            try:
                json.loads(body)
            except json.JSONDecodeError as e:
                return f"Error: Invalid JSON body — {e}"
        data = body.encode("utf-8")
        req_headers.setdefault("Content-Type", content_type or "application/json")

    req = urllib.request.Request(url, data=data, headers=req_headers, method=m)
    ctx = ssl.create_default_context()

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            resp_headers = dict(resp.headers)
            resp_body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            resp_body = e.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            resp_body = ""
        if len(resp_body) > HTTP_MAX_RESPONSE_CHARS:
            resp_body = resp_body[:HTTP_MAX_RESPONSE_CHARS] + (
                f"\n... (truncated, total {len(resp_body)} chars)"
            )
        return f"HTTP Error {e.code} {e.reason}\nBody:\n{resp_body}"
    except urllib.error.URLError as e:
        return f"Connection Error: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return f"Error: {type(e).__name__}: {e}"

    try:
        parsed = json.loads(resp_body)
        resp_body = json.dumps(parsed, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        pass
    if len(resp_body) > HTTP_MAX_RESPONSE_CHARS:
        resp_body = resp_body[:HTTP_MAX_RESPONSE_CHARS] + (
            f"\n... (truncated, total {len(resp_body)} chars)"
        )

    return "\n".join(
        [
            f"Status: {status}",
            f"Headers: {json.dumps(resp_headers, indent=2, ensure_ascii=False)}",
            f"Body:\n{resp_body}",
        ]
    )


def _duckduckgo_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """DuckDuckGo HTML 端点（免费、无需 API key）。"""
    ctx = ssl.create_default_context()
    params = urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(
        "https://html.duckduckgo.com/html/",
        data=params.encode("utf-8"),
        headers={
            "User-Agent": _UA,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    results: list[dict[str, str]] = []
    blocks = re.split(r'<div[^>]*class="result(?: |")', html)[1:]
    for block in blocks:
        title_match = re.search(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL
        )
        if not title_match:
            continue
        url = title_match.group(1)
        redirect = re.search(r"uddg=([^&]+)", url)
        if redirect:
            url = urllib.parse.unquote(redirect.group(1))
        elif url.startswith("//"):
            url = "https:" + url
        title = re.sub(r"<[^>]+>", "", title_match.group(2)).strip()
        snippet = ""
        snippet_match = re.search(
            r'class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL
        )
        if snippet_match:
            snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()
        results.append({"title": title, "url": url, "content": snippet})
        if len(results) >= max_results:
            break
    return results


def _tavily_search(query: str, max_results: int) -> list[dict[str, str]]:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise ValueError("TAVILY_API_KEY not set")
    from tavily import TavilyClient  # type: ignore

    resp = TavilyClient(api_key=api_key).search(query=query, max_results=max_results)
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "content": item.get("content", ""),
        }
        for item in resp.get("results", [])
    ]


def _bing_search(query: str, max_results: int) -> list[dict[str, str]]:
    api_key = os.environ.get("BING_API_KEY")
    if not api_key:
        raise ValueError("BING_API_KEY not set")
    params = urllib.parse.urlencode(
        {
            "q": query,
            "count": min(max_results, 10),
            "responseFilter": "Webpages",
            "textDecorations": "false",
        }
    )
    url = f"https://api.bing.microsoft.com/v7.0/search?{params}"
    req = urllib.request.Request(
        url,
        headers={"Ocp-Apim-Subscription-Key": api_key, "User-Agent": _UA},
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        data = json.loads(resp.read())
    return [
        {
            "title": item.get("name", ""),
            "url": item.get("url", ""),
            "content": item.get("snippet", ""),
        }
        for item in data.get("webPages", {}).get("value", [])[:max_results]
    ]


def _default_searcher(query: str, top_k: int) -> str:
    """默认检索后端：Tavily → Bing → DuckDuckGo（按可用密钥择优）。"""
    engines: list[tuple[str, Callable[[str, int], list[dict[str, str]]]]] = []
    if os.environ.get("TAVILY_API_KEY"):
        engines.append(("tavily", _tavily_search))
    if os.environ.get("BING_API_KEY"):
        engines.append(("bing", _bing_search))
    engines.append(("duckduckgo", _duckduckgo_search))

    errors: list[str] = []
    for name, fn in engines:
        try:
            results = fn(query, top_k)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
            continue
        if results:
            lines = [f"Web search results for: {query}", f"Engine: {name}", ""]
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. {r.get('title', '')}")
                lines.append(f"   URL: {r.get('url', '')}")
                lines.append(f"   {r.get('content', '')}")
                lines.append("")
            return "\n".join(lines).strip()
        errors.append(f"{name}: no results")

    return f"❌ 检索失败：{'; '.join(errors) or 'unknown error'}"


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

    return {"web_search": search, "web_fetch": fetch, "http_request": http_request}


READ_ONLY_WEB_TOOLS = frozenset({"web_search", "web_fetch"})
# http_request 可发 POST/PUT/DELETE 等写操作，必须人工审批。
_APPROVAL_WEB_TOOLS = frozenset({"http_request"})


def register_web_tools(
    registry: Any,
    *,
    searcher: Callable[[str, int], str] | None = None,
    scope: str = "global",
    names: "set[str] | None" = None,
) -> list[str]:
    """把网络工具注册进 `ToolRegistry`，返回注册的工具名列表。

    `web_search`/`web_fetch` 只读放行；`http_request` 标记需人工审批。
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
            require_approval=name in _APPROVAL_WEB_TOOLS,
        )
        registered.append(name)
    return registered


__all__ = [
    "DEFAULT_FETCH_CHARS",
    "DEFAULT_SEARCH_RESULTS",
    "READ_ONLY_WEB_TOOLS",
    "http_request",
    "make_web_tools",
    "register_web_tools",
    "web_fetch",
    "web_search",
]
