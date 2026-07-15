"""
Bridge between plugins and RAG components.

Set during startup by runtime.py so plugins can access the retriever
for KB search without going through app.state.
"""

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.rag.retriever import Retriever
    from app.rag.vector_store import VectorStore

_retriever: Optional["Retriever"] = None
_vector_store: Optional["VectorStore"] = None


def set_retriever(r: "Retriever") -> None:
    global _retriever
    _retriever = r


def get_retriever() -> Optional["Retriever"]:
    return _retriever


def set_vector_store(vs: "VectorStore") -> None:
    global _vector_store
    _vector_store = vs


def get_vector_store() -> Optional["VectorStore"]:
    if _vector_store is not None:
        return _vector_store
    if _retriever is not None:
        return _retriever.vector_store
    return None
