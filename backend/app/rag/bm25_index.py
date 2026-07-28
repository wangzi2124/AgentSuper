"""BM25 关键词索引 — 基于词频的精确匹配检索。

负责：
- 文档分块的关键词索引构建
- 使用 jieba 进行中文分词
- BM25Okapi 算法计算文档相关性
- 与向量检索融合（RRF）提升召回率

使用场景：
- 用户搜索精确关键词时，BM25 比向量检索更有效
- 与向量检索结合使用，兼顾语义理解和精确匹配
"""

import re
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

    def build(self, documents: List[str], metadata: List[dict]):
        """构建 BM25 索引，替换已有数据。"""
        self.documents = documents
        self.metadata = metadata
        self._tokenized = [_tokenize(d) for d in documents]
        self.bm25 = BM25Okapi(self._tokenized)

    def add(self, documents: List[str], metadata: List[dict]):
        """向已有索引追加文档并重建索引。"""
        self.documents.extend(documents)
        self.metadata.extend(metadata)
        self._tokenized.extend(_tokenize(d) for d in documents)
        self.bm25 = BM25Okapi(self._tokenized)

    def search(self, query: str, k: int = 5) -> List[Tuple[dict, float]]:
        """执行 BM25 检索，返回 (文档条目, 得分) 列表。"""
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
