import re
import uuid
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

MARKDOWN_CHILD_MIN = 300

_HEADING_PATTERN = re.compile(r'^(#{1,3})\s+(.+?)\s*#*\s*$', re.MULTILINE)
_FAQ_Q_PATTERN = re.compile(r'^\s*(?:Q[:：]|问[:：]|问题\s*\d*\s*[:：]?)\s*', re.IGNORECASE | re.MULTILINE)
_FAQ_A_PATTERN = re.compile(r'^\s*(?:A[:：]|答[:：]|回答[:：])', re.IGNORECASE | re.MULTILINE)
_TABLE_ROW_PATTERN = re.compile(r'^\s*\|.*\|\s*$')

_PROCESS_TITLE_PATTERN = re.compile(
    r'^\s*(?:流程(?:名称)?|过程|操作流程|作业流程|业务流程|处理流程|SOP)\s*[:：]\s*(.+?)\s*$',
    re.MULTILINE | re.IGNORECASE,
)
_STEP_PATTERN = re.compile(
    r'^\s*(?:'
    r'步骤\s*(?P<cn>[一二三四五六七八九十百千\d]+)'
    r'|Step\s*(?P<en>\d+)'
    r'|(?P<num>\d{1,2})[\.、\)]'
    r'|[（(](?P<pnum>\d{1,2})[)）]'
    r')\s*[:：\s\-]*(?P<title>.*)$',
    re.IGNORECASE | re.MULTILINE,
)
_PROCESS_FIELD_PATTERNS = {
    "role": re.compile(r'(?:角色|负责人|执行人|参与人|岗位)\s*[:：]\s*([^\n；;]+)', re.IGNORECASE),
    "precondition": re.compile(r'(?:前置条件|前提|触发条件)\s*[:：]\s*([^\n]+)', re.IGNORECASE),
    "input": re.compile(r'(?:输入|入参|所需资料|材料)\s*[:：]\s*([^\n]+)', re.IGNORECASE),
    "output": re.compile(r'(?:输出|产出|结果)\s*[:：]\s*([^\n]+)', re.IGNORECASE),
    "exception": re.compile(r'(?:异常|例外|失败处理)\s*[:：]\s*([^\n]+)', re.IGNORECASE),
    "branch": re.compile(r'(?:分支|判断|条件)\s*[:：]\s*([^\n]+)', re.IGNORECASE),
}


class DocumentProcessor:
    """文档处理器：结构感知分块（Markdown/段落/FAQ/表格/流程步骤）。"""

    def __init__(
        self,
        chunk_size: int = MAX_CHUNK_SIZE,
        chunk_overlap: int = CHUNK_OVERLAP,
        parent_summary_length: int = PARENT_SUMMARY_LENGTH,
        department: str = "default",
    ):
        self.chunk_size = int(chunk_size) if chunk_size else MAX_CHUNK_SIZE
        self.chunk_overlap = int(chunk_overlap) if chunk_overlap else CHUNK_OVERLAP
        self.parent_summary_length = parent_summary_length
        self.department = department
        self.paragraph_chunk_size = max(200, self.chunk_size)
        self.paragraph_chunk_overlap = min(max(0, self.chunk_overlap), max(0, self.paragraph_chunk_size - 1))
        self.child_max = max(200, self.chunk_size)
        self.child_min = max(120, min(MARKDOWN_CHILD_MIN, self.child_max - 20))
        self.hard_max = max(self.child_max, self.paragraph_chunk_size)

    def _read_text_file(self, file_path: str) -> str:
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
        if text:
            control = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\n\r")
            if control / len(text) > 0.01:
                raise ValueError(f"File does not appear to be a readable text file: {file_path}")
        return text

    def load(self, file_path: str) -> str:
        ext = Path(file_path).suffix.lower()
        if ext in (".txt", ".md"):
            return self._read_text_file(file_path)
        elif ext == ".pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(file_path)
                return "\n".join(page.extract_text() or "" for page in reader.pages)
            except ImportError:
                raise ImportError("pypdf is required to process PDF files. Install it with: pip install pypdf")
        else:
            raise ValueError(f"Unsupported file type: {ext}")

    def _force_meta(self, meta: dict, source_file: str, section_title: str, parent_id: str, department: str) -> dict:
        out = {**meta}
        out["source_file"] = meta.get("source_file") or source_file
        out["section_title"] = meta.get("section_title") or section_title or source_file
        out["parent_id"] = meta.get("parent_id") or parent_id
        out["department"] = meta.get("department") or department
        return out

    def _hard_split(self, text: str, max_len: int, overlap: int) -> List[Tuple[str, int, int]]:
        text = text or ""
        if not text:
            return []
        max_len = max(50, int(max_len))
        overlap = min(max(0, int(overlap)), max_len - 1)
        out = []
        start = 0
        n = len(text)
        while start < n:
            end = min(start + max_len, n)
            raw = text[start:end]
            piece = raw.strip()
            if piece:
                ltrim = len(raw) - len(raw.lstrip())
                rtrim = len(raw) - len(raw.rstrip())
                out.append((piece, start + ltrim, end - rtrim))
            if end >= n:
                break
            start = end - overlap
        return out

    @staticmethod
    def _find_span(haystack: str, needle: str, start_at: int = 0) -> Tuple[int, int]:
        if not needle:
            return (start_at, start_at)
        idx = haystack.find(needle, start_at)
        if idx >= 0:
            return (idx, idx + len(needle))
        # fallback: locate by first/last significant tokens with flexible whitespace
        tokens = [t for t in re.split(r'\s+', needle.strip()) if t]
        if not tokens:
            return (start_at, min(len(haystack), start_at + len(needle)))
        first = tokens[0]
        i = haystack.find(first, start_at)
        if i < 0:
            return (start_at, min(len(haystack), start_at + len(needle)))
        last = tokens[-1]
        j = haystack.find(last, i)
        if j >= 0:
            j += len(last)
            return (i, min(len(haystack), max(j, i + len(needle.strip()))))
        return (i, min(len(haystack), i + len(needle)))

    @staticmethod
    def _extract_table_blocks_with_spans(text: str) -> List[Tuple[str, int, int]]:
        lines = text.splitlines(keepends=True)
        blocks = []
        cur = []
        cur_start = 0
        pos = 0
        for line in lines:
            is_row = bool(_TABLE_ROW_PATTERN.match(line.rstrip('\r\n')))
            if is_row:
                if not cur:
                    cur_start = pos
                cur.append(line)
            else:
                if len(cur) >= 2:
                    block = "".join(cur).rstrip('\r\n')
                    blocks.append((block, cur_start, cur_start + len(block)))
                cur = []
            pos += len(line)
        if len(cur) >= 2:
            block = "".join(cur).rstrip('\r\n')
            blocks.append((block, cur_start, cur_start + len(block)))
        return blocks

    @staticmethod
    def _remove_spans(text: str, spans: List[Tuple[int, int]]) -> str:
        if not spans:
            return text
        spans = sorted([(max(0, s), min(len(text), e)) for s, e in spans if e > s])
        merged = []
        for s, e in spans:
            if not merged or s > merged[-1][1]:
                merged.append([s, e])
            else:
                merged[-1][1] = max(merged[-1][1], e)
        out = []
        last = 0
        for s, e in merged:
            out.append(text[last:s])
            out.append("\n")
            last = e
        out.append(text[last:])
        return re.sub(r'\n{3,}', '\n\n', "".join(out))

    def _split_chapters(self, text: str) -> List[Tuple[str, str, int]]:
        matches = list(CHAPTER_PATTERN.finditer(text))
        if not matches:
            return [(text, "", 0)]
        chapters = []
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            line_end = text.find('\n', m.end())
            if line_end == -1:
                line_end = m.end()
            title = text[m.start():line_end].strip() or m.group().strip()
            chapters.append((text[start:end].strip(), title, start))
        return chapters

    def _parse_chapter_number(self, chapter_title: str) -> Tuple[int, str]:
        raw = chapter_title
        num_str = re.sub(r'[^\d]', '', raw)
        if num_str:
            try:
                return int(num_str), raw
            except Exception:
                pass
        cn_digits = {'零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
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
                tmp = scale if tmp == 0 else tmp * scale
                result += tmp
                tmp = 0
        result += tmp
        return result if result > 0 else 0, chapter_title

    def _split_markdown_sections(self, text: str) -> List[Tuple[str, str, str, int, int, int]]:
        matches = list(_HEADING_PATTERN.finditer(text))
        if not matches:
            return [("", text, "", 0, 0, 0)]
        sections = []
        if matches[0].start() > 0:
            preamble = text[:matches[0].start()]
            if preamble.strip():
                ls = len(preamble) - len(preamble.lstrip())
                sections.append(("", preamble.strip(), "", 0, ls, ls))
        current_parent = ""
        for i, m in enumerate(matches):
            level = len(m.group(1))
            title = m.group(2).strip()
            body_start = m.end()
            body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            raw_body = text[body_start:body_end]
            body = raw_body.strip()
            if level <= 2:
                current_parent = title
                parent_title = ""
            else:
                parent_title = current_parent
            b_off = body_start + (len(raw_body) - len(raw_body.lstrip())) if body else body_start
            sections.append((title, body, parent_title, level, m.start(), b_off))
        return sections

    def _chunk_markdown_section(self, body: str, metadata: dict, body_offset: int = 0) -> List[Tuple[str, dict]]:
        body = (body or "").strip()
        if not body:
            return []
        # preserve paragraph boundaries as units for better offset fidelity
        units = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]
        expanded = []
        for para in units:
            if len(para) <= self.child_max:
                expanded.append(para)
            else:
                sentences = re.split(r'(?<=[。！？!?；;\n])', para)
                cur = ""
                for s in sentences:
                    s = s.strip()
                    if not s:
                        continue
                    if len(cur) + len(s) > self.child_max and len(cur) >= self.child_min:
                        expanded.append(cur)
                        cur = s
                    else:
                        cur = (cur + s) if cur else s
                if cur:
                    expanded.append(cur)
        chunks = []
        cur = ""
        for u in expanded:
            if cur and len(cur) + len(u) + 2 > self.child_max:
                chunks.append(cur)
                cur = u
            else:
                cur = (cur + "\n\n" + u) if cur else u
        if cur:
            chunks.append(cur)

        out = []
        search_from = 0
        for c in chunks:
            pieces = [(c, 0, len(c))] if len(c) <= self.hard_max else self._hard_split(c, self.child_max, min(40, self.child_max // 8))
            base_pos = body.find(c, search_from)
            if base_pos < 0:
                base_pos, base_end = self._find_span(body, c, search_from)
            else:
                base_end = base_pos + len(c)
            if base_pos >= 0:
                search_from = base_pos + 1
            for piece, rs, re_ in pieces:
                abs_s = body_offset + (base_pos if base_pos >= 0 else 0) + rs
                abs_e = body_offset + (base_pos if base_pos >= 0 else 0) + re_
                out.append((piece, {**metadata, "chunk_start": abs_s, "chunk_end": abs_e}))
        return out

    def _window_chunks(self, text: str, metadata: dict, base_offset: int = 0) -> List[Tuple[str, dict]]:
        text = text.strip()
        if not text:
            return []
        paragraphs = [p for p in re.split(r'\n\s*\n', text) if p.strip()]
        chunks = []
        buf = ""
        size = self.paragraph_chunk_size
        overlap = self.paragraph_chunk_overlap
        for para in paragraphs:
            para = para.strip()
            para_parts = [para] if len(para) <= self.hard_max else [p for p, _, _ in self._hard_split(para, size, overlap)]
            for part in para_parts:
                if buf and len(buf) + len(part) + 2 > size:
                    chunks.append(buf)
                    tail = buf[-overlap:] if overlap > 0 else ""
                    buf = (tail + "\n\n" + part).strip()
                else:
                    buf = (buf + "\n\n" + part).strip() if buf else part
                while len(buf) > size:
                    chunks.append(buf[:size].strip())
                    tail = buf[size - overlap:size] if overlap > 0 else ""
                    buf = (tail + buf[size:]).strip()
        if buf:
            if len(buf) > self.hard_max:
                for p, _, _ in self._hard_split(buf, size, overlap):
                    chunks.append(p)
            else:
                chunks.append(buf)

        out = []
        search_from = 0
        for c in chunks:
            if not c:
                continue
            idx = text.find(c, search_from)
            if idx < 0:
                idx, end = self._find_span(text, c, search_from)
            else:
                end = idx + len(c)
            if idx >= 0:
                search_from = max(search_from, idx + 1
                )
                out.append((c, {**metadata, "chunk_start": base_offset + idx, "chunk_end": base_offset + end}))
            else:
                out.append((c, {**metadata, "chunk_start": base_offset, "chunk_end": base_offset + len(c)}))
        return out

    def _is_faq(self, text: str) -> bool:
        q_count = len(_FAQ_Q_PATTERN.findall(text))
        a_count = len(_FAQ_A_PATTERN.findall(text))
        return q_count >= 1 and a_count >= 1 and q_count >= a_count

    def _split_faq(self, text: str) -> List[Tuple[str, int, int]]:
        lines = text.splitlines(keepends=True)
        blocks = []
        cur = []
        cur_start = 0
        pos = 0
        for line in lines:
            if _FAQ_Q_PATTERN.match(line) and cur:
                block = "".join(cur).strip()
                if block:
                    blocks.append((block, cur_start, cur_start + len("".join(cur).rstrip('\r\n'))))
                cur = [line]
                cur_start = pos
            else:
                if not cur:
                    cur_start = pos
                cur.append(line)
            pos += len(line)
        if cur:
            block = "".join(cur).strip()
            if block:
                blocks.append((block, cur_start, cur_start + len("".join(cur).rstrip('\r\n'))))
        return blocks

    @staticmethod
    def _faq_question(block: str) -> str:
        for line in block.splitlines():
            if _FAQ_Q_PATTERN.match(line):
                return _FAQ_Q_PATTERN.sub("", line).strip()
        return ""

    def _extract_dialogues(self, text: str, base_meta: dict, base_offset: int = 0) -> List[Tuple[str, dict]]:
        anchors = []
        seen = set()
        for m in list(DIALOGUE_PREFIX.finditer(text)) + list(DIALOGUE_SUFFIX.finditer(text)):
            if m.re is DIALOGUE_PREFIX:
                speaker = m.group(1).strip()
                dialogue = m.group(2).strip()
            else:
                dialogue = m.group(1).strip()
                speaker = m.group(2).strip()
            if not speaker or not dialogue:
                continue
            key = f"{speaker}:{dialogue[:50]}"
            if key in seen:
                continue
            seen.add(key)
            text_content = f"{speaker}说：\u201c{dialogue}\u201d"
            anchors.append((text_content, {
                **base_meta, "is_dialogue": True, "speaker": speaker, "dialogue": dialogue,
                "chunk_start": base_offset + m.start(), "chunk_end": base_offset + m.end(),
            }))
        return anchors

    def _is_process_doc(self, text: str) -> bool:
        step_hits = list(_STEP_PATTERN.finditer(text))
        if len(step_hits) < 2:
            return False
        field_hits = sum(1 for pat in _PROCESS_FIELD_PATTERNS.values() if pat.search(text))
        return field_hits >= 1 or bool(_PROCESS_TITLE_PATTERN.search(text)) or len(step_hits) >= 3

    @staticmethod
    def _process_name(text: str, filename: str) -> str:
        m = _PROCESS_TITLE_PATTERN.search(text or "")
        if m:
            return m.group(1).strip()[:80]
        for line in (text or "").splitlines():
            line = line.strip().strip('#').strip()
            if line:
                return line[:80]
        return Path(filename).stem

    @staticmethod
    def _step_no(match: re.Match) -> str:
        for k in ("cn", "en", "num", "pnum"):
            v = match.groupdict().get(k)
            if v:
                return str(v)
        return ""

    @staticmethod
    def _extract_process_fields(block: str) -> dict:
        out = {}
        for key, pat in _PROCESS_FIELD_PATTERNS.items():
            matches = pat.findall(block or "")
            if matches:
                out[key] = matches[-1].strip() if isinstance(matches[-1], str) else str(matches[-1]).strip()
        return out

    def _process_process_doc(self, text: str, doc_id: str, filename: str, dept: str, base_metadata: dict):
        all_chunks = []
        chapter_metas = []
        process_name = self._process_name(text, filename)
        matches = list(_STEP_PATTERN.finditer(text))
        if not matches:
            return self._process_generic(text, doc_id, filename, dept, base_metadata)

        parent_id = f"{doc_id}#proc"
        parent_text = (f"流程：{process_name}\n" + text[:self.parent_summary_length]).strip()
        parent_meta = self._force_meta(
            {**base_metadata, "is_parent": True, "process_name": process_name, "chunk_start": 0, "chunk_end": min(len(text), len(parent_text))},
            filename, process_name, parent_id, dept,
        )
        all_chunks.append((parent_text, parent_meta))
        chapter_metas.append({"document_id": doc_id, "filename": filename, "chapter_number": None, "chapter_title": process_name, "summary": text[:self.parent_summary_length], "parent_chunk_text": parent_text})

        steps = []
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[start:end].strip()
            b_start, b_end = self._find_span(text, block, start)
            title = (m.group('title') or '').strip() or f"步骤{self._step_no(m) or i + 1}"
            steps.append({"idx": i, "step_no": self._step_no(m) or str(i + 1), "title": title, "block": block, "start": b_start, "end": b_end})

        for i, step in enumerate(steps):
            fields = self._extract_process_fields(step["block"])
            section_title = f"{process_name} / 步骤{step['step_no']} {step['title']}".strip()
            meta = self._force_meta({
                **base_metadata, "is_parent": False, "is_process_step": True, "process_name": process_name,
                "step_no": step["step_no"], "step_index": i,
                "prev_step": steps[i - 1]["step_no"] if i > 0 else "", "next_step": steps[i + 1]["step_no"] if i + 1 < len(steps) else "",
                "role": fields.get("role", ""), "precondition": fields.get("precondition", ""), "input": fields.get("input", ""),
                "output": fields.get("output", ""), "exception": fields.get("exception", ""), "branch": fields.get("branch", ""),
            }, filename, section_title, parent_id, dept)
            block = step["block"]
            if len(block) <= self.hard_max:
                all_chunks.append((block, {**meta, "chunk_start": step["start"], "chunk_end": step["end"]}))
            else:
                for piece, rs, re_ in self._hard_split(block, self.paragraph_chunk_size, self.paragraph_chunk_overlap):
                    all_chunks.append((piece, {**meta, "chunk_start": step["start"] + rs, "chunk_end": step["start"] + re_}))
        return all_chunks, chapter_metas

    def process(self, file_path: str, doc_id: str, filename: str, department: str | None = None):
        text = self.load(file_path)
        dept = department or self.department
        ext = Path(file_path).suffix.lower()
        base_metadata = {"document_id": doc_id, "filename": filename, "source": filename, "source_file": filename, "department": dept}

        table_blocks = self._extract_table_blocks_with_spans(text)
        body_text = self._remove_spans(text, [(s, e) for _, s, e in table_blocks]) if table_blocks else text

        if self._is_faq(text):
            all_chunks, chapter_metas = self._process_faq(text, doc_id, filename, dept, base_metadata)
        elif self._is_process_doc(text):
            all_chunks, chapter_metas = self._process_process_doc(body_text or text, doc_id, filename, dept, base_metadata)
        elif ext == ".md":
            all_chunks, chapter_metas = self._process_markdown(body_text, doc_id, filename, dept, base_metadata)
        else:
            all_chunks, chapter_metas = self._process_generic(body_text, doc_id, filename, dept, base_metadata)

        for tbl_idx, (tbl, ts, te) in enumerate(table_blocks):
            meta = self._force_meta({**base_metadata, "is_table": True, "chunk_start": ts, "chunk_end": te}, filename, "表格", f"{doc_id}#table{tbl_idx}", dept)
            all_chunks.append((tbl, meta))
        return all_chunks, chapter_metas

    def _process_markdown(self, text: str, doc_id: str, filename: str, dept: str, base_metadata: dict):
        sections = self._split_markdown_sections(text)
        all_chunks = []
        chapter_metas = []
        from collections import defaultdict
        children_by_parent = defaultdict(list)

        parent_seq = 0
        current_parent_id = ""
        ordered_parent_records = []
        for title, body, parent_title, level, sec_start, body_start in sections:
            if level >= 3 and current_parent_id:
                parent_id = current_parent_id
            else:
                parent_id = f"{doc_id}#s{parent_seq}"
                parent_seq += 1
                current_parent_id = parent_id
            child_meta = self._force_meta({**base_metadata, "is_parent": False}, filename, title, parent_id, dept)
            children = self._chunk_markdown_section(body, child_meta, body_offset=body_start)
            children_by_parent[parent_id].extend(children)
            if level < 3:
                ordered_parent_records.append((parent_id, title, body, sec_start))

        for parent_id, title, body, sec_start in ordered_parent_records:
            summary = (body or "")[:self.parent_summary_length].strip()
            parent_text = f"{title}\n{summary}".strip() if title else summary
            parent_meta = self._force_meta({**base_metadata, "is_parent": True, "chunk_start": sec_start, "chunk_end": sec_start + len(parent_text)}, filename, title, parent_id, dept)
            all_chunks.append((parent_text, parent_meta))
            chapter_metas.append({"document_id": doc_id, "filename": filename, "chapter_number": self._parse_chapter_number(title)[0] if title else None, "chapter_title": title or "", "summary": summary, "parent_chunk_text": parent_text})

        for children in children_by_parent.values():
            all_chunks.extend(children)
        return all_chunks, chapter_metas

    def _process_faq(self, text: str, doc_id: str, filename: str, dept: str, base_metadata: dict):
        all_chunks = []
        chapter_metas = []
        parent_id = f"{doc_id}#faq"
        parent_text = text[:self.parent_summary_length].strip()
        parent_meta = self._force_meta({**base_metadata, "is_parent": True, "chunk_start": 0, "chunk_end": min(len(text), len(parent_text))}, filename, "FAQ", parent_id, dept)
        all_chunks.append((parent_text, parent_meta))
        chapter_metas.append({"document_id": doc_id, "filename": filename, "chapter_number": None, "chapter_title": "FAQ", "summary": parent_text, "parent_chunk_text": parent_text})
        for idx, (block, start, end) in enumerate(self._split_faq(text)):
            question = self._faq_question(block) or f"FAQ{idx + 1}"
            meta = self._force_meta({**base_metadata, "is_parent": False, "is_faq": True, "chunk_start": start, "chunk_end": end}, filename, question, parent_id, dept)
            all_chunks.append((block, meta))
        return all_chunks, chapter_metas

    def _process_generic(self, text: str, doc_id: str, filename: str, dept: str, base_metadata: dict):
        chapters = self._split_chapters(text)
        all_chunks = []
        chapter_metas = []
        for idx, (content, chapter_title, c_start) in enumerate(chapters):
            parent_id = f"{doc_id}#c{idx}"
            meta = {**base_metadata}
            if chapter_title:
                chapter_number, _ = self._parse_chapter_number(chapter_title)
                meta["chapter_title"] = chapter_title
                meta["chapter_number"] = chapter_number
            if c_start < 0:
                c_start = 0
            summary = content[:self.parent_summary_length]
            parent_text = f"{chapter_title}\n{summary}" if chapter_title else summary
            section_title = chapter_title or (content[:20].strip() or filename)
            parent_meta = self._force_meta({**meta, "is_parent": True, "chunk_start": c_start, "chunk_end": c_start + len(parent_text)}, filename, section_title, parent_id, dept)
            all_chunks.append((parent_text, parent_meta))
            chapter_metas.append({"document_id": doc_id, "filename": filename, "chapter_number": meta.get("chapter_number"), "chapter_title": chapter_title or "", "summary": summary, "parent_chunk_text": parent_text})
            child_meta = self._force_meta({**meta, "is_parent": False}, filename, section_title, parent_id, dept)
            all_chunks.extend(self._window_chunks(content, child_meta, base_offset=c_start))
            all_chunks.extend(self._extract_dialogues(content, child_meta, base_offset=c_start))
        return all_chunks, chapter_metas
