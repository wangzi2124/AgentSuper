"""文档处理模块 — 文档解析、分块、元数据提取。

负责：
- 读取 TXT/MD/PDF 文件内容
- 章节检测：正则匹配"第X章"/"Chapter X"识别章节边界
- 父子文档分块：父文档=章节摘要，子文档=正文块
- 对话格式识别：检测对话内容并生成对话级文档块
- 元数据提取：章节号、标题、来源文件等

分块策略：
- 按章节边界分割
- 每章生成父文档（章节标题+前300字摘要）
- 正文按滑动窗口切分（默认500字，重叠200字）
"""

import re
from pathlib import Path
from typing import List, Tuple

CHAPTER_PATTERN = re.compile(
    r'(第[一二三四五六七八九十百千\d]+[章章节回部集])'
    r'|(Chapter\s+\d+)'
    r'|(CHAPTER\s+\d+)',
)

_CN_QUOTE_LEFT = '\u201c'
_CN_QUOTE_RIGHT = '\u201d'

DIALOGUE_PREFIX = re.compile(
    r'([\u4e00-\u9fff\w]{1,8})'
    r'(?:说|道|问|答|喊|叫|骂|嚷|叹|念|读|讲|哭|笑|唱|吼|吟|夸|赞|'
    r'批评|表扬|解释|回答|告诉|吩咐|命令|警告|威胁|劝|安慰|询问|补充|回应|宣布|声明|感叹)'
    r'[：:]\s*'
    r'[' + _CN_QUOTE_LEFT + r'\u300c"\uff02]'
    r'([^' + _CN_QUOTE_RIGHT + r'\u300d"\uff02]{2,200})'
    r'[' + _CN_QUOTE_RIGHT + r'\u300d"\uff02]'
)

DIALOGUE_SUFFIX = re.compile(
    r'[' + _CN_QUOTE_LEFT + r'\u300c"\uff02]'
    r'([^' + _CN_QUOTE_RIGHT + r'\u300d"\uff02]{2,200})'
    r'[' + _CN_QUOTE_RIGHT + r'\u300d"\uff02]'
    r'\s*'
    r'([\u4e00-\u9fff\w]{1,8})'
    r'(?:说|道|问|答|喊|叫|骂|嚷|叹|念|读|讲|哭|笑|唱|吼|吟|夸|赞|解释|回答|告诉|回应|补充|声明)'
)

MAX_CHUNK_SIZE = 500
CHUNK_OVERLAP = 200
PARENT_SUMMARY_LENGTH = 300


class DocumentProcessor:
    """文档处理器，负责加载、分章、分块和对话提取。"""

    def __init__(self, chunk_size: int = MAX_CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def _read_text_file(self, file_path: str) -> str:
        """读取文本文件，自动尝试多种编码。"""
        with open(file_path, "rb") as f:
            data = f.read()

        for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "cp1252", "latin-1"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue

        return data.decode("utf-8", errors="ignore")

    def load(self, file_path: str) -> str:
        """加载文件内容，支持 txt/md/pdf 格式。"""
        ext = Path(file_path).suffix.lower()
        if ext in (".txt", ".md"):
            return self._read_text_file(file_path)
        elif ext == ".pdf":
            try:
                import pypdf

                reader = pypdf.PdfReader(file_path)
                return "\n".join(
                    page.extract_text() for page in reader.pages
                )
            except ImportError:
                raise ImportError(
                    "pypdf is required to process PDF files. "
                    "Install it with: pip install pypdf"
                )
        else:
            raise ValueError(f"Unsupported file type: {ext}")

    def _split_chapters(self, text: str) -> List[Tuple[str, str]]:
        """按章节标题分割文本，返回 [(章节内容, 章节标题), ...]。"""
        matches = list(CHAPTER_PATTERN.finditer(text))
        if not matches:
            return [(text, "")]

        chapters = []
        for i, m in enumerate(matches):
            title = m.group().strip()
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            content = text[start:end].strip()
            chapters.append((content, title))

        return chapters

    def _parse_chapter_number(self, chapter_title: str) -> Tuple[int, str]:
        """从章节标题中解析章节序号，支持阿拉伯数字和中文数字。"""
        raw = chapter_title
        num_str = re.sub(r'[^\d]', '', raw)
        if num_str:
            return int(num_str), raw

        cn_digits = {'零': 0, '一': 1, '二': 2, '三': 3, '四': 4,
                     '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
        cn_scale = {'十': 10, '百': 100, '千': 1000}

        chars = [ch for ch in chapter_title if ch in cn_digits or ch in cn_scale or ch.isdigit()]
        if not chars:
            return 0, chapter_title

        result = 0
        tmp = 0
        for ch in chars:
            if ch.isdigit():
                tmp = int(ch)
            elif ch in cn_digits:
                tmp = cn_digits[ch]
            elif ch in cn_scale:
                scale = cn_scale[ch]
                if tmp == 0:
                    tmp = scale
                else:
                    tmp *= scale
                result += tmp
                tmp = 0
        result += tmp
        return result if result > 0 else 0, chapter_title

    def _chunk_text(self, text: str, metadata: dict) -> List[Tuple[str, dict]]:
        """将文本按固定大小和重叠进行分块。"""
        chunks = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = min(start + self.chunk_size, text_len)
            chunk_text = text[start:end]
            chunk_metadata = {
                **metadata,
                "chunk_start": start,
                "chunk_end": end,
            }
            chunks.append((chunk_text, chunk_metadata))
            if end >= text_len:
                break
            start = end - self.chunk_overlap

        return chunks

    def _extract_dialogues(self, text: str, base_meta: dict) -> List[Tuple[str, dict]]:
        """从文本中提取对话片段，生成对话锚点文档块。"""
        anchors: List[Tuple[str, dict]] = []
        seen: set[str] = set()

        for m in DIALOGUE_PREFIX.finditer(text):
            speaker = m.group(1).strip()
            dialogue = m.group(2).strip()
            if not speaker or not dialogue:
                continue
            key = f"{speaker}:{dialogue[:50]}"
            if key in seen:
                continue
            seen.add(key)
            text_content = f"{speaker}说：“{dialogue}”"
            anchors.append((text_content, {
                **base_meta,
                "is_dialogue": True,
                "speaker": speaker,
                "dialogue": dialogue,
            }))

        for m in DIALOGUE_SUFFIX.finditer(text):
            dialogue = m.group(1).strip()
            speaker = m.group(2).strip()
            if not speaker or not dialogue:
                continue
            key = f"{speaker}:{dialogue[:50]}"
            if key in seen:
                continue
            seen.add(key)
            text_content = f"{speaker}说：“{dialogue}”"
            anchors.append((text_content, {
                **base_meta,
                "is_dialogue": True,
                "speaker": speaker,
                "dialogue": dialogue,
            }))

        return anchors

    def process(
        self, file_path: str, doc_id: str, filename: str
    ) -> Tuple[List[Tuple[str, dict]], List[dict]]:
        """
        Returns:
          all_chunks: list of (text, metadata) for vector store (parent + children + dialogue anchors)
          chapter_metas: list of dicts for ChapterStore
        """
        text = self.load(file_path)
        base_metadata = {
            "document_id": doc_id,
            "filename": filename,
            "source": filename,
        }

        chapters = self._split_chapters(text)
        all_chunks: List[Tuple[str, dict]] = []
        chapter_metas: List[dict] = []

        for content, chapter_title in chapters:
            meta = {**base_metadata}

            if chapter_title:
                chapter_number, _ = self._parse_chapter_number(chapter_title)
                meta["chapter_title"] = chapter_title
                meta["chapter_number"] = chapter_number

            # --- parent chunk: chapter title + summary ---
            summary = content[:PARENT_SUMMARY_LENGTH]
            parent_meta = {
                **meta,
                "is_parent": True,
                "chunk_start": 0,
                "chunk_end": len(summary),
            }
            parent_text = f"{chapter_title}\n{summary}" if chapter_title else summary
            all_chunks.append((parent_text, parent_meta))

            # --- store chapter metadata ---
            chapter_metas.append({
                "document_id": doc_id,
                "filename": filename,
                "chapter_number": meta.get("chapter_number"),
                "chapter_title": chapter_title or "",
                "summary": summary,
                "parent_chunk_text": parent_text,
            })

            # --- child chunks: body text ---
            sub_chunks = self._chunk_text(content, {**meta, "is_parent": False})
            all_chunks.extend(sub_chunks)

            # --- dialogue anchor chunks ---
            dialogue_anchors = self._extract_dialogues(content, {
                **meta,
                "is_parent": False,
            })
            all_chunks.extend(dialogue_anchors)

        return all_chunks, chapter_metas
