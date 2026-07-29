"""运行时初始化模块。

负责应用启动时的组件初始化和依赖注入：
- 加载 .env 环境变量到 os.environ
- 初始化向量存储、嵌入模型、检索器、重排序器
- 加载技能(Skills)和插件(Plugins)
- 创建 RAGAgent 实例
- 管理文档处理和文件存储服务

使用双检锁确保线程安全的单例初始化。
"""

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
_app_state = None


def get_app_state():
    """Get the global app state (for CrewManager etc.)."""
    return _app_state


def get_plugin_loader() -> PluginLoader:
    """Get the plugin loader from the global app state."""
    if _app_state and hasattr(_app_state, "plugin_loader"):
        return _app_state.plugin_loader
    # Fallback: create a new loader
    return PluginLoader(settings.plugins_dir)


def get_skill_loader() -> SkillLoader:
    """Get the skill loader from the global app state."""
    if _app_state and hasattr(_app_state, "skill_loader"):
        return _app_state.skill_loader
    # Fallback: create a new loader
    return SkillLoader(settings.skills_dir)


def _load_env_to_os():
    """将 .env 文件中的配置注入到 os.environ，供插件等模块读取。"""
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
    """从向量库中提取所有文档并构建 BM25 索引。"""
    idx = BM25Index()
    try:
        texts, metadatas = vs.get_all()
        if texts:
            idx.build(texts, metadatas)
    except Exception as e:
        logger.warning("Failed to build BM25 index: %s", e)
    return idx


def ensure_runtime_state(app) -> object:
    """确保运行时状态已初始化（双检锁，线程安全），已初始化则跳过。"""
    if hasattr(app.state, "vector_store") and hasattr(app.state, "file_store"):
        return app.state

    with _init_lock:
        if hasattr(app.state, "vector_store") and hasattr(app.state, "file_store"):
            return app.state
        _do_init(app)
    return app.state


def _do_init(app):
    """执行完整的运行时初始化：加载向量库、嵌入模型、检索器、技能、插件、Agent 等。"""
    global _app_state
    _app_state = app.state
    vs = VectorStore.load(settings.vector_store_path)
    emb = LocalEmbeddings(settings.embedding_model)
    bm25 = _build_bm25_index(vs)
    chapter_store = ChapterStore("data/chapter_store.db")
    retriever = Retriever(vs, emb, bm25_index=bm25, chapter_store=chapter_store)

    from app.rag.plugin_bridge import set_retriever as _bridge_set_retriever
    from app.rag.plugin_bridge import set_vector_store as _bridge_set_vector_store
    _bridge_set_retriever(retriever)
    _bridge_set_vector_store(vs)

    reranker = Reranker(
        model_name=settings.reranker_model,
    ) if settings.enable_reranker else None

    _load_env_to_os()

    skill_loader = SkillLoader(settings.skills_dir)
    skill_loader.load_all()

    plugin_loader = PluginLoader(settings.plugins_dir)
    plugin_loader.load_all()

    perm_mgr = PermissionManager(workspace=str(Path(__file__).resolve().parents[1].parent))
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

    # CrewAI multi-agent module (tools bridged from app's loaders)
    from app.crew.crew_manager import CrewManager
    app.state.crew_manager = CrewManager(
        plugin_loader=plugin_loader,
        skill_loader=skill_loader,
    )

    return app.state
