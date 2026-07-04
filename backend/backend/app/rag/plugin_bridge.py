"""
Bridge between plugins and RAG components.

Set during startup by runtime.py so plugins can access the retriever
for KB search without going through app.state.
"""

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.rag.retriever import Retriever

_retriever: Optional["Retriever"] = None


def set_retriever(r: "Retriever") -> None:
    global _retriever
    _retriever = r


def get_retriever() -> Optional["Retriever"]:
    return _retriever
