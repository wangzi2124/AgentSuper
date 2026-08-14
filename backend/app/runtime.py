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
from app.agent.memory import MemoryManager
from app.agent.rag_wrapper import RAGAgentWrapper
from app.agent.web_search_agent import WebSearchAgent
from app.agent.code_agent import CodeAgent
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

    reranker = None
    if settings.enable_reranker:
        try:
            reranker = Reranker(
                model_name=settings.reranker_model,
            )
        except Exception as e:  # noqa: BLE001
            # 下载/加载失败降级为不重排（Agent 仍可用），避免整个启动失败。
            logger.warning(
                "Reranker disabled: failed to initialize (%s). "
                "Set ENABLE_RERANKER=false or pre-download the model.", e,
            )

    _load_env_to_os()

    skill_loader = SkillLoader(settings.skills_dir)
    skill_loader.load_all()

    plugin_loader = PluginLoader(settings.plugins_dir)
    plugin_loader.load_all()

    # 可写工作目录完全由前端「工作目录」面板配置（持久化于 data/runtime_workspaces.json），
    # 不再支持 .env 的 EXTRA_WORKSPACES。
    # 文件工具的相对路径基准 = 项目 worktree（git 仓库根，见 app/tools/file_tools.py `_workspace()`）；
    # 源码保护（app/plugins/skills/config/main.py 等）仍以 backend/ 为基准判定，
    # 权限层通过 project_worktree 将仓库根下路径识别为 workspace。
    _base_dir = Path(__file__).resolve().parents[1]
    _data_dir = _base_dir / "data"

    # ── opencode 风格文件系统:项目模型 + 分层存储 + 目录扫描缓存 ──
    # 项目:优先 git rev-parse 定位 worktree,ID 取 git 根哈希(持久化到 .git/opencode)
    from app.filesystem import Project, ScanCache, set_project
    from app.storage.paths import global_paths, project_scoped

    project = Project.from_git(_base_dir)
    set_project(project)
    _storage_paths = global_paths(_base_dir)      # data/cache/config/state/log/bin
    _project_storage = project_scoped(project.id)  # 按 projectID 隔离的 session/cache/log
    app.state.project = project
    app.state.project_id = project.id
    app.state.scan_cache = ScanCache()
    app.state.storage_paths = _storage_paths
    app.state.project_storage = _project_storage
    logger.info("project initialized: id=%s worktree=%s", project.id, project.worktree)

    # [token 优化 v6] 自定义工具存储：脚本型写 plugins/custom_*.py（复用插件加载链路），
    # 固定型（pin）写 data/pinned_tools.json（按需挂载时始终保留该工具 schema）
    from app.skills.custom_tools import CustomToolStore
    custom_tools = CustomToolStore(
        plugins_dir=settings.plugins_dir,
        pinned_path=str(_data_dir / "pinned_tools.json"),
    )

    perm_mgr = PermissionManager(
        workspace=str(_base_dir),
        whitelist_path=str(_data_dir / "permissions.json"),
        external_default=settings.external_path_default,
        approval_timeout=settings.permission_approval_timeout,
        allow_source_writes=settings.allow_source_writes,
        project_worktree=project.worktree,
    )
    set_perm_manager(perm_mgr)

    agent = RAGAgent(retriever, skill_loader, plugin_loader, reranker=reranker, custom_tools=custom_tools)

    # ── 初始化多 Agent 系统（bus 创建 + 注册，但 start_all 在异步上下文中调用）──
    agent_bus = AgentBus()
    shared_memory = MemoryManager(default_ttl=300)  # 共享记忆，5 分钟过期

    rag_wrapper = RAGAgentWrapper(agent, agent_id="rag", heartbeat=agent_bus.touch)
    agent_bus.register(rag_wrapper)

    web_search = WebSearchAgent(memory=shared_memory, agent_id="web_search")
    agent_bus.register(web_search)

    code_agent = CodeAgent(memory=shared_memory, agent_id="code")
    agent_bus.register(code_agent)

    supervisor = SupervisorAgent(agent_bus, memory=shared_memory)
    agent_bus.register(supervisor)

    logger.info("Multi-agent system initialized: %s", agent_bus.list_agents())

    app.state.vector_store = vs
    app.state.embeddings = emb
    app.state.agent_bus = agent_bus
    app.state.shared_memory = shared_memory
    app.state.retriever = retriever
    app.state.bm25_index = bm25
    app.state.chapter_store = chapter_store
    app.state.reranker = reranker
    app.state.agent = agent
    app.state.file_store = FileStore(str(_storage_paths["data"] / "uploads"))
    app.state.doc_processor = DocumentProcessor(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    app.state.skill_loader = skill_loader
    app.state.plugin_loader = plugin_loader
    app.state.custom_tools = custom_tools
    app.state.task_manager = TaskManager()
    return app.state
