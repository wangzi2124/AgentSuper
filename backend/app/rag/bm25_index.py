import re
import threading
from typing import List, Tuple, Optional

try:
    import jieba
except ImportError:
    jieba = None

from rank_bm25 import BM25Okapi


def _tokenize(text: str) -> List[str]:
    """对文本进行分词，优先使用 jieba，不可用时按非字符分割。"""
    if jieba:
        return list(jieba.cut(text))
    return [t for t in re.split(r'\W+', text) if t]


class BM25Index:
    """基于 BM25Okapi 的关键词检索索引。"""

    def __init__(self):
        self.bm25: Optional[BM25Okapi] = None
        self.documents: List[str] = []
        self.metadata: List[dict] = []
        self._tokenized: List[List[str]] = []
        # 保护共享索引结构：上传/删除（执行器线程）与检索可并发访问
        self._lock = threading.RLock()

    def build(self, documents: List[str], metadata: List[dict]):
        """构建 BM25 索引，替换已有数据。"""
        with self._lock:
            self.documents = documents
            self.metadata = metadata
            self._tokenized = [_tokenize(d) for d in documents]
            self.bm25 = BM25Okapi(self._tokenized)

    def add(self, documents: List[str], metadata: List[dict]):
        """向已有索引追加文档并重建索引。"""
        with self._lock:
            self.documents.extend(documents)
            self.metadata.extend(metadata)
            self._tokenized.extend(_tokenize(d) for d in documents)
            self.bm25 = BM25Okapi(self._tokenized)

    def remove_by_metadata(self, key: str, value) -> None:
        """按元数据删除索引中的文档条目（如删除文档后同步 BM25）。"""
        with self._lock:
            kept_docs = []
            kept_meta = []
            for doc, meta in zip(self.documents, self.metadata):
                if meta.get(key) != value:
                    kept_docs.append(doc)
                    kept_meta.append(meta)
            self.documents = kept_docs
            self.metadata = kept_meta
            self._tokenized = [_tokenize(d) for d in self.documents]
            self.bm25 = BM25Okapi(self._tokenized) if self.documents else None

    def search(self, query: str, k: int = 5) -> List[Tuple[dict, float]]:
        """执行 BM25 检索，返回 (文档条目, 得分) 列表。"""
        with self._lock:
            if not self.bm25 or not self.documents:
                return []
            tokenized_query = _tokenize(query)
            scores = self.bm25.get_scores(tokenized_query)
            indexed = [(i, scores[i]) for i in range(len(scores))]
            indexed.sort(key=lambda x: x[1], reverse=True)
            results = []
            for i, score in indexed[:k]:
                if score > 0:
                    results.append(({
                        "text": self.documents[i],
                        "metadata": self.metadata[i],
                    }, float(score)))
            return results
