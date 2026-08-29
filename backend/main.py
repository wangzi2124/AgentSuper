import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api import documents, chat, skills, plugins, vectors, generated, permission as perm_api, config, weather, auth as auth_api, custom_tools as custom_tools_api, voice as voice_api
from app.api.responses import ApiError, error_response
from app.auth import AuthMiddleware
from app.config import settings
from app.monitor import RequestLogMiddleware, get_stats
from app.runtime import ensure_runtime_state
from app.session import SessionService, init_db
from app.session import task_bridge
from app.session.router import router as sessions_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理，启动时初始化运行时状态。"""
    await asyncio.to_thread(ensure_runtime_state, app)
    # 启动清空：VECTOR_STORE_AUTO_CLEAR=true 时清空全部知识库数据（向量+章节+BM25+上传文件）
    if settings.vector_store_auto_clear:
        from app.services.kb_cleanup import clear_all_kb

        result = await clear_all_kb(app)
        logging.getLogger(__name__).info(
            "Auto-clear on startup: %s", result,
        )
    # Session 管理（session.db）：建表（多 Agent 直写落库，无单 Agent executor）
    init_db()
    app.state.session_service = SessionService()
    # 启动 Agent Bus 事件循环（需要在主事件循环中调用 asyncio.create_task）
    agent_bus = getattr(app.state, "agent_bus", None)
    if agent_bus:
        task_bridge.bind_bus(agent_bus)
        agent_bus.start_all()
    weather.load_weather_on_startup()
    # 清理过期的工具输出截断文件（data/truncation，保留期 7 天，对齐 opencode truncate.ts）
    try:
        from app.context.tool_output import cleanup_truncated

        cleanup_truncated()
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning("Truncated output cleanup failed: %s", e)
    # 定时 TTL 清理：VECTOR_STORE_TTL_DAYS>0 时按间隔定期清理过期文档；
    # 后台维护循环同时执行知识库自愈（D2：index_state!=ready 的文档重放建索引）。
    _maintenance_task = None

    async def _maintenance_loop():
        interval = max(settings.vector_store_cleanup_interval_hours, 1) * 3600
        logger = logging.getLogger(__name__)
        while True:
            await asyncio.sleep(interval)
            try:
                if settings.vector_store_ttl_days > 0:
                    from app.services.kb_cleanup import clear_expired

                    removed = await clear_expired(app)
                    if removed:
                        logger.info("Scheduled TTL cleanup removed %d expired documents", removed)
            except Exception as e:  # noqa: BLE001
                logger.warning("Scheduled TTL cleanup failed: %s", e)
            try:
                from app.services.kb_repair import repair_incomplete_documents

                result = await repair_incomplete_documents(app)
                if result["repaired"]:
                    logger.info("Scheduled KB repair: %s", result)
            except Exception as e:  # noqa: BLE001
                logger.warning("Scheduled KB repair failed: %s", e)

    _maintenance_task = asyncio.create_task(_maintenance_loop())

    # 启动一次性自愈：上次进程崩溃/索引中断留下的半成品文档在启动时补齐，
    # 以背景任务运行（不阻塞服务就绪），重放建索引与上传共用串行锁。
    _repair_task = None

    async def _startup_repair():
        from app.services.kb_repair import repair_incomplete_documents

        try:
            result = await repair_incomplete_documents(app)
            if result["repaired"]:
                logging.getLogger(__name__).info("Startup KB repair completed: %s", result)
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).warning("Startup KB repair failed: %s", e)

    # 无待修复文档时 repair 内部立即返回，成本忽略不计
    _repair_task = asyncio.create_task(_startup_repair())

    try:
        yield
    except asyncio.CancelledError:
        pass
    finally:
        if _maintenance_task:
            _maintenance_task.cancel()
        if _repair_task:
            _repair_task.cancel()


app = FastAPI(
    title="Knowledge Base System",
    description="RAG-powered knowledge base with AI agent (LangChain + LangGraph + ChromaDB)",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestLogMiddleware)  # type: ignore
app.add_middleware(AuthMiddleware)  # type: ignore


# ── 统一异常 → 统一响应体 {code, message, data, detail} ─────────────────────
@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return error_response(exc.code, exc.message, exc.status, exc.data)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False) if detail is not None else "请求失败"
    return error_response(exc.status_code, detail, exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return error_response(422, "请求参数校验失败", 422)


app.include_router(auth_api.router, prefix="/api/auth", tags=["Auth"])
app.include_router(documents.router, prefix="/api/documents", tags=["Documents"])
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])
app.include_router(sessions_router, prefix="/api/sessions", tags=["Sessions"])
app.include_router(skills.router, prefix="/api/skills", tags=["Skills"])
app.include_router(plugins.router, prefix="/api/plugins", tags=["Plugins"])
app.include_router(custom_tools_api.router, prefix="/api/custom-tools", tags=["Custom Tools"])
app.include_router(vectors.router, prefix="/api/vectors", tags=["Vectors"])
app.include_router(generated.router, prefix="/api/generated", tags=["Generated"])
app.include_router(perm_api.router, prefix="/api", tags=["Permission"])
app.include_router(config.router, prefix="/api/config", tags=["Config"])
app.include_router(weather.router, prefix="/api", tags=["Weather"])
app.include_router(voice_api.router, prefix="/api/voice", tags=["Voice"])


@app.get("/")
async def root():
    """根路径，返回服务基本信息。"""
    return {
        "service": "Knowledge Base System",
        "version": "0.1.0",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """健康检查接口，返回服务状态和向量库大小。"""
    if not hasattr(app.state, "vector_store"):
        return {"status": "initializing"}
    return {"status": "ok", "vector_store_size": app.state.vector_store.count}


@app.get("/api/monitor/stats")
async def monitor_stats():
    """获取系统监控统计信息。"""
    return get_stats()
