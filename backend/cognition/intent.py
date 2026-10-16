import re
from typing import Optional

CN_NUM_MAP = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "百": 100, "千": 1000,
}

_CHAPTER_CN_RE = re.compile(r"第?([一二三四五六七八九十百千\d零]+)[章章节回部集]")
_CHAPTER_EN_RE = re.compile(r"[Cc]hapter\s+(\d+)")
_CHAPTER_KEYWORD_RE = re.compile(
    r"(?:关于|查找|搜索|找|介绍|说|看|讲|里面).{0,15}(?:章|节|回|部|集)"
    r"|(?:章|节|回|部|集).{0,10}(?:内容|说了|讲了|是什么|在哪|在哪页)"
    r"|(?:内容|摘要|概要|剧情).{0,5}(?:章|节|回|部|集)"
)


def _cn_to_int(s: str) -> int:
    """将中文数字字符串转换为整数。"""
    result = 0
    temp = 0
    for ch in s:
        if ch in CN_NUM_MAP:
            n = CN_NUM_MAP[ch]
            if n >= 10:
                result += n * (temp if temp else 1)
                temp = 0
            else:
                temp += n
    result += temp
    return result


def detect_chapter_intent(query: str) -> Optional[dict]:
    """
    Returns {chapter_number: int|None, chapter_title_raw: str} if query
    mentions a specific chapter, otherwise None.
    """
    m = _CHAPTER_CN_RE.search(query)
    if m:
        ns = m.group(1).strip()
        if ns.isdigit():
            return {"chapter_number": int(ns), "chapter_title_raw": m.group()}
        return {"chapter_number": _cn_to_int(ns), "chapter_title_raw": m.group()}

    m = _CHAPTER_EN_RE.search(query)
    if m:
        return {"chapter_number": int(m.group(1)), "chapter_title_raw": m.group()}

    m = _CHAPTER_KEYWORD_RE.search(query)
    if m:
        return {"chapter_number": None, "chapter_title_raw": m.group()}

    return None
