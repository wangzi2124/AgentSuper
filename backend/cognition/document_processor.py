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
    r'([\u4e00-\u9fff\w]{1,20})'
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
    r'([\u4e00-\u9fff\w]{1,20})'
    r'(?:说|道|问|答|喊|叫|骂|嚷|叹|念|读|讲|哭|笑|唱|吼|吟|夸|赞|解释|回答|告诉|回应|补充|声明)'
)

MAX_CHUNK_SIZE = 500
CHUNK_OVERLAP = 200
PARENT_SUMMARY_LENGTH = 300

# --- agent-2.md §13.2 结构感知分块参数 ---
# Markdown/技术文档：按标题层级（H2→H3）切分，父块 800~1200、子块 300~400 字符。
MARKDOWN_PARENT_MIN = 800
MARKDOWN_PARENT_MAX = 1200
MARKDOWN_CHILD_MIN = 300
MARKDOWN_CHILD_MAX = 400
# PDF/Word：按段落 + 重叠窗口，512 token（≈512 字符近似），overlap 10~15%。
PARAGRAPH_CHUNK_SIZE = 512
PARAGRAPH_CHUNK_OVERLAP = 64

# Markdown 标题：H1~H3（# / ## / ###）。H2 视作父小节，H3 视作子小节。
_HEADING_PATTERN = re.compile(r'^(#{1,3})\s+(.+?)\s*#*\s*$', re.MULTILINE)
# FAQ / Q&A：Q: / A: 或 问： / 答： 或 问题N ...
_FAQ_Q_PATTERN = re.compile(r'^\s*(?:Q[:：]|问[:：]|问题\s*\d*\s*[:：]?)\s*', re.IGNORECASE)
_FAQ_A_PATTERN = re.compile(r'^\s*(?:A[:：]|答[:：]|回答[:：])\s*', re.IGNORECASE)
# Markdown 表格：连续以 | 开头/结尾的行
_TABLE_ROW_PATTERN = re.compile(r'^\s*\|.*\|\s*$')


class DocumentProcessor:
    """文档处理器，负责加载、分章、分块和对话提取。

    §13.2 结构感知分块：按文档类型选择策略（Markdown 标题层级 / PDF·Word 段落重叠 /
    FAQ 一问一答 / 表格单独成块），每个 chunk 强制携带 parent_id / source_file /
    section_title / department 四个 metadata 字段。
    """

    def __init__(self, chunk_size: int = MAX_CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP, parent_summary_length: int = PARENT_SUMMARY_LENGTH, department: str = "default"):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.parent_summary_length = parent_summary_length
        self.department = department

    def _read_text_file(self, file_path: str) -> str:
        """读取文本文件，自动尝试多种编码。

        不含 latin-1 兜底：latin-1 对所有字节都能解码，会把损坏/二进制文件
        静默当作正常文本入库，因此只尝试常见真实文本编码。
        """
        with open(file_path, "rb") as f:
            data = f.read()

        for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "cp1252"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = data.decode("utf-8", errors="ignore")

        # 乱码检测：不可打印控制字符占比过高时视为非文本文件
        if text:
            control = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\n\r")
            if control / len(text) > 0.01:
                raise ValueError(f"File does not appear to be a readable text file: {file_path}")
        return text

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

    # ------------------------------------------------------------------
    # §13.2 结构感知分块
    # ------------------------------------------------------------------

    def _force_meta(self, meta: dict, source_file: str, section_title: str,
                    parent_id: str, department: str) -> dict:
        """补齐 §13.2.1 的强制 metadata 字段（不覆盖已有显式值，除非为空）。"""
        out = {**meta}
        out["source_file"] = meta.get("source_file") or source_file
        out["section_title"] = meta.get("section_title") or section_title
        out["parent_id"] = meta.get("parent_id") or parent_id
        out["department"] = meta.get("department") or department
        return out

    def _split_markdown_sections(self, text: str) -> List[Tuple[str, str, str, int]]:
        """按 H1~H3 标题切分 Markdown。

        Returns: [(section_title, body_text, parent_title, level), ...]
        - H2 作为父小节（父）标题；H3 归属于最近的 H2（level=3）。
        - 无标题的正文归入 section_title=""、level=0 的默认小节。
        """
        matches = list(_HEADING_PATTERN.finditer(text))
        if not matches:
            return [("", text, "", 0)]

        sections: List[Tuple[str, str, str, int]] = []
        # 标题之前的引言正文
        if matches[0].start() > 0:
            preamble = text[:matches[0].start()].strip()
            if preamble:
                sections.append(("", preamble, "", 0))

        current_parent = ""
        for i, m in enumerate(matches):
            level = len(m.group(1))
            title = m.group(2).strip()
            body_start = m.end()
            body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[body_start:body_end].strip()

            if level <= 2:
                current_parent = title
                parent_title = ""
            else:
                parent_title = current_parent
            sections.append((title, body, parent_title, level))

        return sections

    def _window_chunks(
        self, text: str, metadata: dict,
        size: int, overlap: int,
    ) -> List[Tuple[str, dict]]:
        """按段落 + 重叠窗口切分（PDF/Word 策略）。优先在段落边界断开。"""
        text = text.strip()
        if not text:
            return []
        paragraphs = [p for p in re.split(r'\n\s*\n', text) if p.strip()]
        chunks: List[Tuple[str, dict]] = []
        buf = ""
        for para in paragraphs:
            if buf and len(buf) + len(para) + 2 > size:
                chunks.append(buf)
                # 重叠窗口：保留尾部 overlap 字符
                tail = buf[-overlap:] if overlap > 0 else ""
                buf = (tail + "\n\n" + para).strip()
            else:
                buf = (buf + "\n\n" + para).strip() if buf else para
            # 单段本身超长 → 硬切
            while len(buf) > size:
                chunks.append(buf[:size])
                tail = buf[size - overlap:size] if overlap > 0 else ""
                buf = (tail + buf[size:]).strip()
        if buf:
            chunks.append(buf)
        return [(c, {**metadata, "chunk_start": 0, "chunk_end": len(c)}) for c in chunks]

    def _chunk_markdown_section(
        self, body: str, metadata: dict,
    ) -> List[Tuple[str, dict]]:
        """子块 300~400 字符：在 section 内按句子/段落累积到目标长度。"""
        body = body.strip()
        if not body:
            return []
        # 先按段落分；段落过大再按句切分
        units: List[str] = []
        for para in re.split(r'\n\s*\n', body):
            para = para.strip()
            if not para:
                continue
            if len(para) <= MARKDOWN_CHILD_MAX:
                units.append(para)
            else:
                # 中文句末标点切句，累积到接近子块上限
                sentences = re.split(r'(?<=[。！？!?；;])', para)
                cur = ""
                for s in sentences:
                    s = s.strip()
                    if not s:
                        continue
                    if len(cur) + len(s) > MARKDOWN_CHILD_MAX and len(cur) >= MARKDOWN_CHILD_MIN:
                        units.append(cur)
                        cur = s
                    else:
                        cur = (cur + s) if cur else s
                if cur:
                    units.append(cur)
        chunks: List[Tuple[str, dict]] = []
        cur = ""
        for u in units:
            if cur and len(cur) + len(u) + 1 > MARKDOWN_CHILD_MAX:
                chunks.append(cur)
                cur = u
            else:
                cur = (cur + "\n" + u) if cur else u
        if cur:
            chunks.append(cur)
        return [(c, {**metadata, "chunk_start": 0, "chunk_end": len(c)}) for c in chunks]

    def _is_faq(self, text: str) -> bool:
        """启发式判断：FAQ/Q&A 文档（出现成对的 Q:/A: 或 问：/答：）。"""
        q_count = len(re.findall(r'^\s*(?:Q[:：]|问[:：]|问题\s*\d*\s*[:：]?)', text, re.MULTILINE | re.IGNORECASE))
        a_count = len(re.findall(r'^\s*(?:A[:：]|答[:：]|回答[:：])', text, re.MULTILINE | re.IGNORECASE))
        return q_count >= 1 and a_count >= 1 and q_count >= a_count

    def _split_faq(self, text: str) -> List[str]:
        """一问一答为一个 chunk（不拆分）。以 Q 行为起点聚合到下一个 Q 之前。"""
        lines = text.splitlines()
        blocks: List[str] = []
        cur: List[str] = []
        for line in lines:
            if _FAQ_Q_PATTERN.match(line) and cur:
                blocks.append("\n".join(cur).strip())
                cur = [line]
            else:
                cur.append(line)
        if cur:
            blocks.append("\n".join(cur).strip())
        return [b for b in blocks if b]

    def _faq_question(self, block: str) -> str:
        """从 FAQ 块中提取问题行作为 section_title（去 Q:/问： 前缀）。"""
        for line in block.splitlines():
            if _FAQ_Q_PATTERN.match(line):
                return _FAQ_Q_PATTERN.sub("", line).strip()
        return ""

    def _extract_table_blocks(self, text: str) -> List[str]:
        """提取连续的 Markdown 表格行，作为一个独立块（保留原 Markdown 表格）。"""
        lines = text.splitlines()
        blocks: List[str] = []
        cur: List[str] = []
        for line in lines:
            if _TABLE_ROW_PATTERN.match(line):
                cur.append(line)
            else:
                if len(cur) >= 2:
                    blocks.append("\n".join(cur))
                cur = []
        if len(cur) >= 2:
            blocks.append("\n".join(cur))
        return blocks

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
        self, file_path: str, doc_id: str, filename: str,
        department: str | None = None,
    ) -> Tuple[List[Tuple[str, dict]], List[dict]]:
        """
        Returns:
          all_chunks: list of (text, metadata) for vector store (parent + children + dialogue anchors)
          chapter_metas: list of dicts for ChapterStore

        §13.2：按文档类型选择分块策略，chunk 均带 parent_id/source_file/section_title/department。
        """
        text = self.load(file_path)
        dept = department or self.department
        ext = Path(file_path).suffix.lower()
        base_metadata = {
            "document_id": doc_id,
            "filename": filename,
            "source": filename,
            "source_file": filename,
            "department": dept,
        }

        all_chunks: List[Tuple[str, dict]] = []
        chapter_metas: List[dict] = []

        if self._is_faq(text):
            all_chunks, chapter_metas = self._process_faq(
                text, doc_id, filename, dept, base_metadata
            )
        elif ext == ".md":
            all_chunks, chapter_metas = self._process_markdown(
                text, doc_id, filename, dept, base_metadata
            )
        else:
            all_chunks, chapter_metas = self._process_generic(
                text, doc_id, filename, dept, base_metadata
            )

        # 表格块：无论何种文档，Markdown 表格单独成 chunk（§13.2.1）
        for tbl_idx, tbl in enumerate(self._extract_table_blocks(text)):
            meta = self._force_meta(
                {**base_metadata, "is_table": True},
                filename, "表格", f"{doc_id}#table{tbl_idx}", dept,
            )
            all_chunks.append((tbl, meta))

        return all_chunks, chapter_metas

    def _process_markdown(
        self, text: str, doc_id: str, filename: str, dept: str, base_metadata: dict,
    ) -> Tuple[List[Tuple[str, dict]], List[dict]]:
        """Markdown：按 H2→H3 标题层级，父块 800~1200 / 子块 300~400 字符。"""
        sections = self._split_markdown_sections(text)
        all_chunks: List[Tuple[str, dict]] = []
        chapter_metas: List[dict] = []

        from collections import defaultdict
        children_by_parent: dict = defaultdict(list)
        section_records: List[dict] = []

        parent_seq = 0
        current_parent_id = ""
        for title, body, parent_title, level in sections:
            if level >= 3 and current_parent_id:
                # H3（及更深）作为子小节：复用所在 H2 父块的 parent_id
                parent_id = current_parent_id
            else:
                parent_id = f"{doc_id}#s{parent_seq}"
                parent_seq += 1
                current_parent_id = parent_id
            record = {
                "parent_id": parent_id,
                "section_title": title,
                "parent_title": parent_title,
                "level": level,
                "body": body,
            }
            section_records.append(record)
            children = self._chunk_markdown_section(
                body,
                self._force_meta(
                    {**base_metadata, "is_parent": False},
                    filename, title, parent_id, dept,
                ),
            )
            children_by_parent[parent_id].extend(children)

        # 父块：父小节（H2/H1 及无标题段）标题 + 正文摘要
        for record in section_records:
            parent_id = record["parent_id"]
            title = record["section_title"]
            if record["level"] >= 3:
                # H3 小节不单独产父块（其父为 H2 小节）
                continue
            body = record["body"]
            summary = body[:MARKDOWN_PARENT_MAX].strip()
            parent_text = f"{title}\n{summary}".strip() if title else summary
            parent_meta = self._force_meta(
                {**base_metadata, "is_parent": True, "chunk_start": 0,
                 "chunk_end": len(summary)},
                filename, title, parent_id, dept,
            )
            all_chunks.append((parent_text, parent_meta))
            chapter_metas.append({
                "document_id": doc_id,
                "filename": filename,
                "chapter_number": self._parse_chapter_number(title)[0] if title else None,
                "chapter_title": title or "",
                "summary": summary,
                "parent_chunk_text": parent_text,
            })

        # 子块：写入全部子块（带 parent_id 指向所属小节）
        for children in children_by_parent.values():
            all_chunks.extend(children)

        return all_chunks, chapter_metas

    def _process_faq(
        self, text: str, doc_id: str, filename: str, dept: str, base_metadata: dict,
    ) -> Tuple[List[Tuple[str, dict]], List[dict]]:
        """FAQ/Q&A：一问一答为一个 chunk，不拆分。"""
        all_chunks: List[Tuple[str, dict]] = []
        chapter_metas: List[dict] = []
        blocks = self._split_faq(text)
        for idx, block in enumerate(blocks):
            parent_id = f"{doc_id}#faq{idx}"
            question = self._faq_question(block)
            meta = self._force_meta(
                {**base_metadata, "is_parent": False, "is_faq": True},
                filename, question or f"FAQ{idx + 1}", parent_id, dept,
            )
            all_chunks.append((block, {**meta, "chunk_start": 0, "chunk_end": len(block)}))
            chapter_metas.append({
                "document_id": doc_id,
                "filename": filename,
                "chapter_number": None,
                "chapter_title": "",
                "summary": block[:self.parent_summary_length],
                "parent_chunk_text": block,
            })
        return all_chunks, chapter_metas

    def _process_generic(
        self, text: str, doc_id: str, filename: str, dept: str, base_metadata: dict,
    ) -> Tuple[List[Tuple[str, dict]], List[dict]]:
        """PDF/Word/纯文本：先按章节（第X章/Chapter X）分章，无章节时按段落+重叠窗口。"""
        chapters = self._split_chapters(text)
        all_chunks: List[Tuple[str, dict]] = []
        chapter_metas: List[dict] = []

        for idx, (content, chapter_title) in enumerate(chapters):
            parent_id = f"{doc_id}#c{idx}"
            meta = {**base_metadata}
            if chapter_title:
                chapter_number, _ = self._parse_chapter_number(chapter_title)
                meta["chapter_title"] = chapter_title
                meta["chapter_number"] = chapter_number

            # 父块：章节标题 + 摘要
            summary = content[:self.parent_summary_length]
            parent_meta = self._force_meta(
                {**meta, "is_parent": True, "chunk_start": 0,
                 "chunk_end": len(summary)},
                filename, chapter_title, parent_id, dept,
            )
            parent_text = f"{chapter_title}\n{summary}" if chapter_title else summary
            all_chunks.append((parent_text, parent_meta))

            chapter_metas.append({
                "document_id": doc_id,
                "filename": filename,
                "chapter_number": meta.get("chapter_number"),
                "chapter_title": chapter_title or "",
                "summary": summary,
                "parent_chunk_text": parent_text,
            })

            # 子块：段落 + 重叠窗口（512 字符 / overlap≈12%）
            child_meta = self._force_meta(
                {**meta, "is_parent": False},
                filename, chapter_title, parent_id, dept,
            )
            all_chunks.extend(
                self._window_chunks(content, child_meta,
                                    PARAGRAPH_CHUNK_SIZE, PARAGRAPH_CHUNK_OVERLAP)
            )

            # 对话锚点
            dialogue_anchors = self._extract_dialogues(content, {
                **self._force_meta(child_meta, filename, chapter_title, parent_id, dept),
            })
            all_chunks.extend(dialogue_anchors)

        return all_chunks, chapter_metas
