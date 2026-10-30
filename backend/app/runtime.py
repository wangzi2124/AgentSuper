import logging
import threading
from pathlib import Path

from config import settings
from execution.permission import PermissionManager, set_manager as set_perm_manager
from extensions.plugins import PluginLoader
from cognition.bm25_index import BM25Index
from cognition.chapter_store import ChapterStore
from cognition.document_processor import DocumentProcessor
from cognition.embeddings import LocalEmbeddings
from cognition.reranker import Reranker
from cognition.retriever import Retriever
from cognition.vector_store import VectorStore
from app.services.task_manager import TaskManager
from app.services.voice import VoiceService
from storage.file_store import FileStore
from storage.paths import data_path, global_paths, project_scoped
from app.snapshot import Snapshot  # [snapshot 快照层] 规格模块 B

logger = logging.getLogger(__name__)

_init_lock = threading.Lock()


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
    # 先解析统一数据根（可能触发一次性旧数据迁移），再打开任何数据库/目录。
    _storage_paths = global_paths()
    _data_dir = _storage_paths["data"]
    logger.info(
        "data root: %s (cache=%s config=%s state=%s log=%s bin=%s)",
        _data_dir,
        _storage_paths["cache"],
        _storage_paths["config"],
        _storage_paths["state"],
        _storage_paths["log"],
        _storage_paths["bin"],
    )
    vs = VectorStore.load(str(data_path(settings.vector_store_path, "vector_store")))
    emb = LocalEmbeddings(settings.embedding_model)
    bm25 = _build_bm25_index(vs)
    chapter_store = ChapterStore(str(data_path(None, "chapter_store.db")))
    retriever = Retriever(vs, emb, bm25_index=bm25, chapter_store=chapter_store)

    from cognition.plugin_bridge import set_retriever as _bridge_set_retriever
    from cognition.plugin_bridge import set_vector_store as _bridge_set_vector_store
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

    # [DeepSeek 兼容补丁] litellm 的 deepseek provider：(1) 会把 content 列表扁平化、
    # 丢弃 image_url 分块；(2) 思考模式下不回填 assistant 的 reasoning_content（未知
    # 模型 supports_reasoning()=False 门控失效）。安装两个补丁修复（详见模块 docstring）。
    from models.litellm_vision_patch import (
        install_litellm_vision_patch,
        install_litellm_reasoning_patch,
    )
    install_litellm_vision_patch()
    install_litellm_reasoning_patch()

    plugin_loader = PluginLoader(settings.plugins_dir)
    plugin_loader.load_all()

    _base_dir = Path(__file__).resolve().parents[1]

    # 技能源：受管库 <data>/skills（自动创建）+ 用户追加的外部源（持久化于
    # <data>/skills_sources.json）+ 随包发布的内置技能（app/skills/bundled/）。
    # 开箱即有可用空库，不再出现「目录不存在」WARNING。
    from app.skills.loader import SkillLoader
    from app.skills import registry as skills_registry

    _managed, _extras = skills_registry.load_sources()
    # 内置技能播种进受管库：幂等/非破坏/尊重删除（详见 registry.seed_bundled）
    _seeded = skills_registry.seed_bundled(_managed)
    skill_loader = SkillLoader(
        _managed, create=True, extra_dirs=_extras,
        bundled_names=skills_registry.bundled_names(_managed),
    )
    skill_loader.load_all()
    skills_registry.save_sources(str(skill_loader.skills_dir), skill_loader.extra_dir_strings())
    logger.info(
        "Skills: managed=%s extras=%s bundled=%d (%d skills)",
        skill_loader.skills_dir, skill_loader.extra_dir_strings() or "[]",
        len(skill_loader.bundled_names), len(skill_loader.list()),
    )
    if _seeded:
        logger.info("Seeded bundled skills: %s", ", ".join(_seeded[:10]) + ("…" if len(_seeded) > 10 else ""))

    # 免审目录只有三类：会话默认临时目录、程序内部数据目录、用户「工作目录白名单」
    # （持久化于 <data>/runtime_workspaces.json，前端「用户设置」维护）。
    # 文件工具的相对路径基准 = 项目 worktree（git 仓库根）；
    # 源码保护（app/plugins/skills/config/main.py 等）仍以 backend/ 为基准判定。⚠️ 仓库根
    # 不再自动免审，且白名单也压不过源码保护（写源码仍需逐次审批）。

    # ── opencode 风格文件系统:项目模型 + 分层存储 + 目录扫描缓存 ──
    # 项目:优先 git rev-parse 定位 worktree,ID 取 git 根哈希(持久化到 .git/opencode)
    from app.filesystem import Project, ScanCache, set_project

    project = Project.from_git(_base_dir)
    set_project(project)
    _project_storage = project_scoped(project.id)  # 按 projectID 隔离的 session/cache/log
    app.state.project = project
    app.state.project_id = project.id
    app.state.scan_cache = ScanCache()
    app.state.storage_paths = _storage_paths
    app.state.project_storage = _project_storage
    logger.info("project initialized: id=%s worktree=%s", project.id, project.worktree)

    # [snapshot 快照层] 规格模块 B：针对项目 worktree 的步骤级快照/回滚。
    # 内部 git 仓库只写对象（write-tree），不建 commit、不碰用户 index/分支/HEAD → 零干扰；
    # 由 chat 轮次钩子（`app/snapshot/` snapshot_begin/snapshot_end）在每次用户请求前/后自动 track，
    # diff_full 出「本次改动的文件 + 行数」随 assistant 消息展示（原 /api/snapshot 管理路由已移除）。
    app.state.snapshot = Snapshot(worktree=project.worktree, data_dir=str(_data_dir))

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
        approval_timeout=settings.permission_approval_timeout,
        allow_source_writes=settings.allow_source_writes,
        project_worktree=project.worktree,
    )
    set_perm_manager(perm_mgr)

    # [语音] 本地 Qwen3-TTS 服务（子进程，无外部 HTTP）：转写（ASR）与合成（TTS）共用同一
    # service，但**互相独立**——TTS 由 VOICE_TTS_ENABLED 控制（会下载 Qwen3-TTS 模型并常驻
    # worker），ASR 只要引擎（faster-whisper/openai-whisper）+ 模型就绪即可用，不再被 TTS
    # 总开关连坐（此前 voice_service=None → 只想语音输入也一律 503）。
    voice_service = VoiceService()
    if settings.voice_tts_enabled:
        # 后台线程启动下载 TTS 模型（同向量模型：ModelScope 优先，失败降级不阻断启动）
        threading.Thread(target=voice_service.ensure_models, daemon=True, name="voice-model-dl").start()
    # 预热常驻 Whisper worker：后台启动并要求模型加载完成，把「首字延迟」（模型加载 ~4s）
    # 从用户点麦克风时挪到服务启动阶段，录音刚开始即可出字。whisper_ready 为假时内部直接返回。
    threading.Thread(target=voice_service.warmup, daemon=True, name="voice-whisper-warmup").start()
    logger.info(
        "Voice service ready (dir=%s, tts_enabled=%s, asr_ready=%s)",
        voice_service.tts_dir, settings.voice_tts_enabled, voice_service.whisper_ready,
    )

    app.state.vector_store = vs
    app.state.embeddings = emb
    app.state.retriever = retriever
    app.state.bm25_index = bm25
    app.state.chapter_store = chapter_store
    app.state.reranker = reranker
    app.state.file_store = FileStore(str(_storage_paths["data"] / "uploads"))
    app.state.doc_processor = DocumentProcessor(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        department=settings.kb_department,
    )
    app.state.plugin_loader = plugin_loader
    app.state.skill_loader = skill_loader
    app.state.custom_tools = custom_tools
    app.state.voice_service = voice_service
    app.state.task_manager = TaskManager()
    return app.state
