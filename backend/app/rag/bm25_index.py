import re
from typing import List, Tuple, Optional

try:
    import jieba
except ImportError:
    jieba = None

from rank_bm25 import BM25Okapi


def _tokenize(text: str) -> List[str]:
    if jieba:
        return list(jieba.cut(text))
    return [t for t in re.split(r'\W+', text) if t]


class BM25Index:
    def __init__(self):
        self.bm25: Optional[BM25Okapi] = None
        self.documents: List[str] = []
        self.metadata: List[dict] = []

    def build(self, documents: List[str], metadata: List[dict]):
        self.documents = documents
        self.metadata = metadata
        tokenized = [_tokenize(d) for d in documents]
        self.bm25 = BM25Okapi(tokenized)

    def add(self, documents: List[str], metadata: List[dict]):
        self.documents.extend(documents)
        self.metadata.extend(metadata)
        tokenized = [_tokenize(d) for d in self.documents]
        self.bm25 = BM25Okapi(tokenized)

    def search(self, query: str, k: int = 5) -> List[Tuple[dict, float]]:
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
