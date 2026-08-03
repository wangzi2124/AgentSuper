"""
Internet Search Plugin

Provides web search capabilities using multiple engines:
- Tavily API (international, requires API key)
- Bing Search (supports both CN and international)
- DuckDuckGo (free, no API key needed)
"""
import json
import os
import urllib.request
import urllib.parse
from typing import Literal

PLUGIN_NAME = "internet-search"
PLUGIN_VERSION = "0.2.0"
PLUGIN_DESCRIPTION = "Search the internet using multiple engines (Tavily/Bing/DuckDuckGo)"


def _bing_search(query: str, max_results: int = 5, market: str = "zh-CN") -> list:
    """Search using Bing API (requires BING_API_KEY)."""
    api_key = os.environ.get("BING_API_KEY")
    if not api_key:
        raise ValueError("BING_API_KEY not set")
    
    endpoint = "https://api.bing.microsoft.com/v7.0/search"
    params = urllib.parse.urlencode({
        "q": query,
        "count": min(max_results, 10),
        "mkt": market,
        "responseFilter": "Webpages",
        "textDecorations": "false",
    })
    url = f"{endpoint}?{params}"
    
    req = urllib.request.Request(url, headers={
        "Ocp-Apim-Subscription-Key": api_key,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    
    ctx = __import__("ssl").create_default_context()
    
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        data = json.loads(resp.read())
    
    results = []
    for item in data.get("webPages", {}).get("value", [])[:max_results]:
        results.append({
            "title": item.get("name", ""),
            "url": item.get("url", ""),
            "content": item.get("snippet", ""),
        })
    return results


def _duckduckgo_search(query: str, max_results: int = 5) -> list:
    """Search using DuckDuckGo HTML endpoint (free, no key needed).

    The Instant Answer API (api.duckduckgo.com) only returns curated
    instant answers, so we parse the regular HTML results page instead.
    """
    import re

    ctx = __import__("ssl").create_default_context()

    params = urllib.parse.urlencode({"q": query})
    url = "https://html.duckduckgo.com/html/"

    req = urllib.request.Request(url, data=params.encode("utf-8"), headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })

    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    results = []
    # Each result is a <div class="result ..."> container. Split on the exact
    # container opener (not result__body / result__a, which also contain "result").
    blocks = re.split(r'<div[^>]*class="result(?: |")', html)[1:]

    for block in blocks:
        title_match = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not title_match:
            continue

        url = title_match.group(1)
        redirect = re.search(r'uddg=([^&]+)', url)
        if redirect:
            url = urllib.parse.unquote(redirect.group(1))
        elif url.startswith("//"):
            url = "https:" + url

        title = re.sub(r"<[^>]+>", "", title_match.group(2)).strip()

        snippet = ""
        snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
        if snippet_match:
            snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()

        results.append({"title": title, "url": url, "content": snippet})
        if len(results) >= max_results:
            break

    return results


def _baidu_search(query: str, max_results: int = 5) -> list:
    """Search using Baidu (scraping approach for domestic search)."""
    params = urllib.parse.urlencode({"wd": query, "rn": min(max_results, 10), "ie": "utf-8"})
    url = f"https://www.baidu.com/s?{params}"
    
    ctx = __import__("ssl").create_default_context()
    
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    
    results = []
    import re
    
    blocks = re.findall(r'<div class="result c-container[^"]*"[^>]*>(.*?)</div>\s*</div>', html, re.DOTALL)
    
    for block in blocks[:max_results]:
        title_match = re.search(r'<h3[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if title_match:
            url = title_match.group(1)
            title = re.sub(r'<[^>]+>', '', title_match.group(2)).strip()
            
            abstract_match = re.search(r'<span class="content-right_[^"]*"[^>]*>(.*?)</span>', block, re.DOTALL)
            if not abstract_match:
                abstract_match = re.search(r'<div class="c-abstract[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
            
            content = re.sub(r'<[^>]+>', '', abstract_match.group(1)).strip() if abstract_match else ""
            
            results.append({"title": title, "url": url, "content": content})
    
    return results


def _tavily_search(query: str, max_results: int = 5, topic: str = "general") -> list:
    """Search using Tavily API (requires TAVILY_API_KEY)."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise ValueError("TAVILY_API_KEY not set")
    
    from tavily import TavilyClient
    client = TavilyClient(api_key=api_key)
    
    response = client.search(
        query=query,
        max_results=max_results,
        topic=topic,
    )
    
    results = []
    for item in response.get("results", []):
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "content": item.get("content", ""),
        })
    return results


def tool_internet_search(
    query: str,
    max_results: int = 5,
    engine: Literal["auto", "tavily", "bing", "baidu", "duckduckgo"] = "auto",
    topic: Literal["general", "news", "finance"] = "general",
    region: Literal["cn", "global"] = "global",
) -> str:
    """Search the internet using multiple search engines.
    
    Use this for general web searches, news, or finding information.
    Do NOT use for fetching content from a specific URL — use tool_extract_urls instead.
    
    Parameters:
    - query: the search query string
    - max_results: number of results to return (1-10)
    - engine: search engine to use
      - 'auto': automatically select best engine
      - 'tavily': international search (requires TAVILY_API_KEY)
      - 'bing': Microsoft Bing (requires BING_API_KEY)
      - 'duckduckgo': free, no API key needed
      - 'baidu': best-effort scraping (heavily anti-bot; usually returns nothing)
    - topic: search category (only for tavily)
    - region: 'cn' for Chinese content, 'global' for international
    """
    errors = []
    results = []
    
    if engine == "auto":
        if region == "cn":
            engines = ["tavily", "bing", "duckduckgo"]
        else:
            engines = ["tavily", "bing", "duckduckgo"]
    else:
        engines = [engine]
    
    for eng in engines:
        try:
            if eng == "tavily":
                results = _tavily_search(query, max_results, topic)
            elif eng == "bing":
                market = "zh-CN" if region == "cn" else "en-US"
                results = _bing_search(query, max_results, market)
            elif eng == "baidu":
                results = _baidu_search(query, max_results)
            elif eng == "duckduckgo":
                results = _duckduckgo_search(query, max_results)
            
            if results:
                break
        except Exception as e:
            errors.append(f"{eng}: {e}")
            continue
    
    if not results:
        return f"No results found. Errors: {'; '.join(errors)}" if errors else "No results found."
    
    lines = [f"Web search results for: {query}", f"Engine: {eng}", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   URL: {r['url']}")
        lines.append(f"   {r['content']}")
        lines.append("")
    
    return "\n".join(lines)


def tool_extract_urls(
    urls: str,
    extract_depth: Literal["basic", "advanced"] = "advanced",
    format: Literal["markdown", "text"] = "markdown",
    timeout: int = 30,
) -> str:
    """Fetch and extract the content of one or more specific web pages by URL.
    Use this when the user wants to read the content of a specific website/URL.
    Do NOT use tool_execute with curl/wget for fetching web pages.
    
    Parameters:
    - urls: one or more URLs separated by commas
    - extract_depth: 'basic' for quick extraction, 'advanced' for full page content
    - format: output format - 'markdown' or 'text'
    - timeout: max wait time in seconds (default 30, max 60)
    """
    ctx = __import__("ssl").create_default_context()
    
    url_list = [u.strip() for u in urls.split(",") if u.strip()]
    if not url_list:
        return "Error: no valid URLs provided"
    
    if timeout > 60:
        timeout = 60
    
    lines = [f"Extracted content for: {', '.join(url_list)}", ""]
    
    for url in url_list:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                content = resp.read().decode("utf-8", errors="ignore")
            
            import re
            title_match = re.search(r"<title[^>]*>(.*?)</title>", content, re.DOTALL | re.IGNORECASE)
            title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else "Untitled"
            
            content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL | re.IGNORECASE)
            content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL | re.IGNORECASE)
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"\s+", " ", content).strip()
            
            if len(content) > 5000:
                content = content[:5000] + "..."
            
            lines.append(f"URL: {url}")
            lines.append(f"Title: {title}")
            lines.append("")
            lines.append(content)
            lines.append("")
        except Exception as e:
            lines.append(f"URL: {url}")
            lines.append(f"Error: {e}")
            lines.append("")
    
    return "\n".join(lines).strip()
