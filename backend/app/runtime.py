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

# ── 多 Agent 系统 ──
from app.agent.bus import AgentBus
from app.agent.rag_wrapper import RAGAgentWrapper
from app.agent.supervisor import SupervisorAgent

logger = logging.getLogger(__name__)

_init_lock = threading.Lock()


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

    # ── 初始化多 Agent 系统（bus 创建 + 注册，但 start_all 在异步上下文中调用）──
    agent_bus = AgentBus()
    rag_wrapper = RAGAgentWrapper(agent, agent_id="rag")
    agent_bus.register(rag_wrapper)
    supervisor = SupervisorAgent(agent_bus)
    agent_bus.register(supervisor)
    logger.info("Multi-agent system initialized: %s", agent_bus.list_agents())

    app.state.vector_store = vs
    app.state.embeddings = emb
    app.state.agent_bus = agent_bus
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
