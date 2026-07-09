import logging
import os
import threading
from pathlib import Path

from app.config import settings
from app.agent.graph import RAGAgent
from app.permission import PermissionManager, set_manager as set_perm_manager
from app.plugins.loader import PluginLoader
from app.rag.bm25_index import BM25Index
from app.rag.chapter_store import ChapterStore
from app.rag.document_processor import DocumentProcessor
from app.rag.embeddings import LocalEmbeddings
from app.rag.reranker import Reranker
from app.rag.retriever import Retriever
from app.rag.vector_store import VectorStore
from app.services.task_manager import TaskManager
from app.skills.loader import SkillLoader
from app.storage.file_store import FileStore

logger = logging.getLogger(__name__)

_init_lock = threading.Lock()


def _load_env_to_os():
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip("\"'")
            if key not in os.environ:
                os.environ[key] = val


def _build_bm25_index(vs: VectorStore) -> BM25Index:
    idx = BM25Index()
    try:
        texts, metadatas = vs.get_all()
        if texts:
            idx.build(texts, metadatas)
    except Exception as e:
        logger.warning("Failed to build BM25 index: %s", e)
    return idx


def ensure_runtime_state(app) -> object:
    if hasattr(app.state, "vector_store") and hasattr(app.state, "file_store"):
        return app.state

    with _init_lock:
        if hasattr(app.state, "vector_store") and hasattr(app.state, "file_store"):
            return app.state
        _do_init(app)
    return app.state


def _do_init(app):
    vs = VectorStore.load(settings.vector_store_path)
    emb = LocalEmbeddings(settings.embedding_model)
    bm25 = _build_bm25_index(vs)
    chapter_store = ChapterStore("data/chapter_store.db")
    retriever = Retriever(vs, emb, bm25_index=bm25, chapter_store=chapter_store)

    from app.rag.plugin_bridge import set_retriever as _bridge_set_retriever
    _bridge_set_retriever(retriever)

    reranker = Reranker(
        model_name=settings.reranker_model,
    ) if settings.enable_reranker else None

    _load_env_to_os()

    skill_loader = SkillLoader(settings.skills_dir)
    skill_loader.load_all()

    plugin_loader = PluginLoader(settings.plugins_dir)
    plugin_loader.load_all()

    perm_mgr = PermissionManager(workspace=str(Path(__file__).resolve().parents[1]))
    set_perm_manager(perm_mgr)

    agent = RAGAgent(retriever, skill_loader, plugin_loader, reranker=reranker)

    app.state.vector_store = vs
    app.state.embeddings = emb
    app.state.retriever = retriever
    app.state.bm25_index = bm25
    app.state.chapter_store = chapter_store
    app.state.reranker = reranker
    app.state.agent = agent
    app.state.file_store = FileStore(settings.upload_dir)
    app.state.doc_processor = DocumentProcessor(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    app.state.skill_loader = skill_loader
    app.state.plugin_loader = plugin_loader
    app.state.task_manager = TaskManager()
    return app.state
