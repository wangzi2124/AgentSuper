"""
Web Extract Plugin

批量抓取 URL 正文并转为可读文本。搜索能力已由内置
`web_search` / `web_fetch` 提供，这里只保留“多 URL 批量提取”这一独有能力。
"""
import re
import ssl
import urllib.request
from typing import Literal

from utils.ssrf import validate_http_url

PLUGIN_NAME = "internet-search"
PLUGIN_VERSION = "0.3.0"
PLUGIN_DESCRIPTION = "Fetch and extract readable text from one or more web pages"


def _html_to_text(html: str, fmt: str) -> str:
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    if fmt == "markdown":
        html = re.sub(
            r"<h([1-6])[^>]*>(.*?)</h\1>",
            lambda m: "#" * int(m.group(1)) + " " + re.sub(r"<[^>]+>", "", m.group(2)).strip() + "\n",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
        html = re.sub(r"</(p|div|li|tr)>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"[ \t\r\f\v]+", " ", html)
    html = re.sub(r"\n\s*\n+", "\n\n", html)
    return html.strip()


def tool_extract_urls(
    urls: str,
    extract_depth: Literal["basic", "advanced"] = "advanced",
    format: Literal["markdown", "text"] = "markdown",
    timeout: int = 30,
) -> str:
    """Fetch and extract the content of one or more specific web pages by URL.
    Use this when the user wants to read the content of specific websites/URLs.
    Do NOT use shell curl/wget for fetching web pages.

    Parameters:
    - urls: one or more URLs separated by commas
    - extract_depth: 'basic' for a short extract, 'advanced' for fuller page content
    - format: output format - 'markdown' or 'text'
    - timeout: max wait time in seconds (default 30, max 60)
    """
    ctx = ssl.create_default_context()

    url_list = [u.strip() for u in urls.split(",") if u.strip()]
    if not url_list:
        return "Error: no valid URLs provided"

    if timeout > 60:
        timeout = 60
    max_chars = 2000 if extract_depth == "basic" else 5000

    lines = [f"Extracted content for: {', '.join(url_list)}", ""]

    for url in url_list:
        err = validate_http_url(url)
        if err:
            lines.extend([f"URL: {url}", f"Error: {err}", ""])
            continue
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                },
            )
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                content = resp.read().decode("utf-8", errors="ignore")

            title_match = re.search(r"<title[^>]*>(.*?)</title>", content, re.DOTALL | re.IGNORECASE)
            title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else "Untitled"

            content = _html_to_text(content, format)
            if len(content) > max_chars:
                content = content[:max_chars] + "..."

            lines.extend([f"URL: {url}", f"Title: {title}", "", content, ""])
        except Exception as e:  # noqa: BLE001
            lines.extend([f"URL: {url}", f"Error: {e}", ""])

    return "\n".join(lines).strip()
