"""认知层（对齐 agent.md 第 4 章 / §9 目录结构）。

RAG 管线 / 混合检索 / 重排序 / 上下文压缩 / 记忆读写。
"""

from .compressor import (
    COMPRESS_THRESHOLD,
    KEEP_RECENT_TOKENS,
    SELF_CHECKLIST,
    build_compress_prompt,
    compress_context,
    estimate_tokens,
    select_tail,
    split_by_token,
)
from .rag import RAGPipeline, rag_search
from .reranker import Reranker, RerankerUnavailable
from .retriever import BM25Retriever, HybridRetriever, HybridSearchRetriever

# `.memory` 已自包含（MemoryManager 由 app.agent.memory 迁入，仅依赖 config）。
# 仍保持惰性解析（PEP 562）：避免导入 `cognition` 包时急切拉起 langgraph Store 等依赖。
_LAZY_MEMORY = {"LongTermMemory", "MemoryManager"}

# 基础设施（向量库 / 嵌入 / BM25 / 文档处理 / 插件桥接）按需惰性解析：
# 顶层导入会急切拉起 chromadb / sentence_transformers（torch），拖慢冷启动。
_LAZY_INFRA = {
    "VectorStore": ("vector_store", "VectorStore"),
    "CHROMA_MAX_BATCH_SIZE": ("vector_store", "CHROMA_MAX_BATCH_SIZE"),
    "BM25Index": ("bm25_index", "BM25Index"),
    "LocalEmbeddings": ("embeddings", "LocalEmbeddings"),
    "DocumentProcessor": ("document_processor", "DocumentProcessor"),
    "ChapterStore": ("chapter_store", "ChapterStore"),
    "get_retriever": ("plugin_bridge", "get_retriever"),
    "get_vector_store": ("plugin_bridge", "get_vector_store"),
    "set_retriever": ("plugin_bridge", "set_retriever"),
    "set_vector_store": ("plugin_bridge", "set_vector_store"),
}


def __getattr__(name: str):
    if name in _LAZY_MEMORY:
        from . import memory

        return getattr(memory, name)
    if name in _LAZY_INFRA:
        mod_name, attr = _LAZY_INFRA[name]
        import importlib

        mod = importlib.import_module(f".{mod_name}", __name__)
        return getattr(mod, attr)
    raise AttributeError(f"module 'cognition' has no attribute {name!r}")


__all__ = [
    "COMPRESS_THRESHOLD",
    "KEEP_RECENT_TOKENS",
    "SELF_CHECKLIST",
    "build_compress_prompt",
    "compress_context",
    "estimate_tokens",
    "select_tail",
    "split_by_token",
    "LongTermMemory",
    "MemoryManager",
    "RAGPipeline",
    "rag_search",
    "Reranker",
    "RerankerUnavailable",
    "BM25Retriever",
    "HybridRetriever",
    "HybridSearchRetriever",
    "VectorStore",
    "CHROMA_MAX_BATCH_SIZE",
    "BM25Index",
    "LocalEmbeddings",
    "DocumentProcessor",
    "ChapterStore",
    "get_retriever",
    "get_vector_store",
    "set_retriever",
    "set_vector_store",
]
