"""
Internet Search Plugin

Provides web search capabilities using Tavily API.
"""
import os
from typing import Literal
from tavily import TavilyClient

PLUGIN_NAME = "internet-search"
PLUGIN_VERSION = "0.1.0"
PLUGIN_DESCRIPTION = "Search the internet for real-time information using Tavily"

_tavily_client = None


def _get_client() -> TavilyClient:
    global _tavily_client
    if _tavily_client is None:
        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            raise ValueError(
                "TAVILY_API_KEY not set. Add it to backend/.env and restart."
            )
        _tavily_client = TavilyClient(api_key=api_key)
    return _tavily_client


def tool_internet_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
) -> str:
    """Search the internet using keywords. Use this for general web searches, news, or finding information.
    Do NOT use for fetching content from a specific URL — use tool_extract_urls instead.
    Do NOT use for weather queries — use get_weather instead.

    Parameters:
    - query: the search query string
    - max_results: number of results to return (1-10)
    - topic: search category - 'general', 'news', or 'finance'
    - include_raw_content: include full page content if true
    """


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
    client = _get_client()
    url_list = [u.strip() for u in urls.split(",") if u.strip()]
    if not url_list:
        return "Error: no valid URLs provided"
    if timeout > 60:
        timeout = 60
    try:
        response = client.extract(
            urls=url_list,
            extract_depth=extract_depth,
            format=format,
            timeout=timeout,
        )
        results = response.get("results", [])
        if not results:
            return f"No content extracted from: {urls}"
        lines = [f"Extracted content for: {', '.join(url_list)}", ""]
        for r in results:
            url = r.get("url", "")
            title = r.get("title", "Untitled")
            content = r.get("raw_content", r.get("content", ""))
            lines.append(f"URL: {url}")
            lines.append(f"Title: {title}")
            lines.append("")
            lines.append(content)
            lines.append("")
        return "\n".join(lines).strip()
    except Exception as e:
        return f"Error extracting content: {e}"
    client = _get_client()
    response = client.search(
        query=query,
        max_results=max_results,
        include_raw_content=include_raw_content,
        topic=topic,
    )
    results = response.get("results", [])
    if not results:
        return "No results found."

    lines = [f"Web search results for: {query}", ""]
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        content = r.get("content", "")
        lines.append(f"{i}. {title}")
        lines.append(f"   URL: {url}")
        lines.append(f"   {content}")
        if include_raw_content and r.get("raw_content"):
            lines.append(f"   [Raw content available, {len(r['raw_content'])} chars]")
        lines.append("")
    return "\n".join(lines)
