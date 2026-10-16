"""平台 FastAPI 入口（对齐 agent.md §9 app/main.py）。

独立于既有 `backend/main.py`，但对前端 100% 可用：`include_app=True`（uvicorn 生产模式）
时复用既有 `app` 的全部路由 + 运行时（RAG/向量/模型/语音/技能/插件/监控…）+ 全部中间件
（Auth / RequestLog / ChainLog），仅把 `/api/chat/*` 与 `/api/sessions/*` 换成平台图实现
（compat.py / sessions.py）。

测试用 `create_app(..., include_app=False)` 得到轻量 app（仅平台路由 + 注入假模型），
避免加载 ChromaDB/嵌入模型等重运行时；此时默认不挂鉴权等中间件（`enable_middleware`）。

启动：
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Callable

# 先在 import 任何 app 模块之前注入 .env 到 os.environ（对齐既有 runtime 约定）
from env_boot import load_env_to_os  # noqa: E402

load_env_to_os()

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from app.api.responses import ApiError, error_response  # noqa: E402
from app.auth import AuthMiddleware  # noqa: E402
from observability.chainlog import ChainLogMiddleware  # noqa: E402
from observability.monitor import RequestLogMiddleware, get_recent_stats, get_stats  # noqa: E402

from .api import admin, chat, compat, sessions  # noqa: E402
from .deps import build_context  # noqa: E402

logger = logging.getLogger(__name__)


def _include_app_routers(app: FastAPI) -> None:
    """挂载既有 `app` 的全部路由（不含 chat/sessions —— 由平台图实现覆盖）。"""
    from app.api import (
        auth as auth_api,
        config,
        custom_tools as custom_tools_api,
        documents,
        generated,
        logs as logs_api,
        models as models_api,
        permission as perm_api,
        plugins,
        skills,
        vectors,
        voice as voice_api,
        weather,
    )

    app.include_router(auth_api.router, prefix="/api/auth", tags=["Auth"])
    app.include_router(documents.router, prefix="/api/documents", tags=["Documents"])
    app.include_router(skills.router, prefix="/api/skills", tags=["Skills"])
    app.include_router(plugins.router, prefix="/api/plugins", tags=["Plugins"])
    app.include_router(custom_tools_api.router, prefix="/api/custom-tools", tags=["Custom Tools"])
    app.include_router(vectors.router, prefix="/api/vectors", tags=["Vectors"])
    app.include_router(generated.router, prefix="/api/generated", tags=["Generated"])
    app.include_router(perm_api.router, prefix="/api", tags=["Permission"])
    app.include_router(config.router, prefix="/api/config", tags=["Config"])
    app.include_router(weather.router, prefix="/api", tags=["Weather"])
    app.include_router(models_api.router, prefix="/api", tags=["Models"])
    app.include_router(voice_api.router, prefix="/api/voice", tags=["Voice"])
    app.include_router(logs_api.router)


async def _init_app_runtime(app: FastAPI) -> None:
    """初始化既有 `app` 运行时（对齐 backend/main.py 的 lifespan 关键步骤）。"""
    from config import settings
    from app.runtime import ensure_runtime_state
    from app.session import SessionService, init_db

    await asyncio.to_thread(ensure_runtime_state, app)

    if settings.vector_store_auto_clear:
        try:
            from app.services.kb_cleanup import clear_all_kb

            result = await clear_all_kb(app)
            logger.info("Auto-clear on startup: %s", result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto-clear on startup failed: %s", exc)

    from storage.migrations import run_migrations

    run_migrations()
    init_db()
    from models import catalog_db

    catalog_db.init()
    if not hasattr(app.state, "session_service"):
        app.state.session_service = SessionService()

    from app.api import weather as weather_api

    try:
        weather_api.load_weather_on_startup()
    except Exception as exc:  # noqa: BLE001
        logger.warning("weather startup failed: %s", exc)

    # 清理过期的工具输出截断文件 / 轮次级快照归档（保留期 7 天）
    try:
        from cognition.tool_output import cleanup_truncated

        cleanup_truncated()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Truncated output cleanup failed: %s", exc)
    try:
        from app.snapshot.turn import cleanup_turn_archives

        cleanup_turn_archives()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Turn snapshot archives cleanup failed: %s", exc)

    # 后台预热模型目录（首次 get_catalog 会同步探测 ollama / 各 provider，避免首屏白等）
    def _warm_model_catalog() -> None:
        try:
            from models import catalog as _catalog

            n = len(_catalog.get_catalog())
            logger.info("Model catalog warmed: %d entries", n)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Model catalog warmup failed: %s", exc)

    asyncio.get_running_loop().run_in_executor(None, _warm_model_catalog)

    if settings.chain_log_enabled:
        try:
            from observability.chainlog.tracer import store as chain_log_store

            chain_log_store().start()
            removed = chain_log_store().cleanup()
            logger.info("Chain log cleanup on startup: %s", removed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Chain log startup failed: %s", exc)


def _start_background(app: FastAPI) -> list[asyncio.Task]:
    """启动后台维护循环 + 启动自愈任务（对齐 backend/main.py），返回任务列表。"""
    from config import settings

    tasks: list[asyncio.Task] = []

    async def _maintenance_loop() -> None:
        interval = max(settings.vector_store_cleanup_interval_hours, 1) * 3600
        while True:
            await asyncio.sleep(interval)
            try:
                if settings.vector_store_ttl_days > 0:
                    from app.services.kb_cleanup import clear_expired

                    removed = await clear_expired(app)
                    if removed:
                        logger.info("Scheduled TTL cleanup removed %d expired documents", removed)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Scheduled TTL cleanup failed: %s", exc)
            try:
                from app.services.kb_repair import repair_incomplete_documents

                result = await repair_incomplete_documents(app.state)
                if result["repaired"]:
                    logger.info("Scheduled KB repair: %s", result)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Scheduled KB repair failed: %s", exc)
            if settings.chain_log_enabled:
                try:
                    from observability.chainlog.tracer import store as chain_log_store

                    chain_log_store().cleanup()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Scheduled chain log cleanup failed: %s", exc)

    async def _startup_repair() -> None:
        try:
            from app.services.kb_repair import repair_incomplete_documents

            result = await repair_incomplete_documents(app.state)
            if result["repaired"]:
                logger.info("Startup KB repair completed: %s", result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Startup KB repair failed: %s", exc)

    tasks.append(asyncio.create_task(_maintenance_loop()))
    tasks.append(asyncio.create_task(_startup_repair()))
    return tasks


async def _shutdown_app_runtime(app: FastAPI) -> None:
    """关停运行时资源（语音 worker / 全链路日志 flush）。"""
    try:
        vsvc = getattr(app.state, "voice_service", None)
        if vsvc is not None and hasattr(vsvc, "shutdown"):
            await asyncio.to_thread(vsvc.shutdown)
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice worker shutdown failed: %s", exc)
    try:
        from observability.chainlog.tracer import store as chain_log_store

        chain_log_store().stop(flush=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Chain log shutdown failed: %s", exc)


def create_app(
    *,
    include_app: bool = False,
    enable_middleware: bool | None = None,
    model: Any | None = None,
    registry: Any | None = None,
    vfs: Any | None = None,
    retrieve: Callable[..., list[dict[str, Any]]] | None = None,
    reranker: Any | None = None,
) -> FastAPI:
    """构建平台 FastAPI 应用。

    include_app=True：复用既有 `app` 全量路由 + 运行时 + 中间件（前端 100% 可用，uvicorn 用）。
    include_app=False：仅平台路由（测试用，注入假模型/VFS）。
    enable_middleware：是否挂 Auth/RequestLog/ChainLog（默认跟随 include_app）。
    """
    if enable_middleware is None:
        enable_middleware = include_app

    options: dict[str, Any] = {
        "model": model,
        "registry": registry,
        "vfs": vfs,
        "retrieve": retrieve,
        "reranker": reranker,
    }

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks: list[asyncio.Task] = []
        if include_app:
            await _init_app_runtime(app)
            tasks = _start_background(app)
        logger.info("building platform context...")
        app.state.platform = build_context(**options)
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            if include_app:
                await _shutdown_app_runtime(app)

    app = FastAPI(
        title="Agent Platform",
        version="1.0.0",
        lifespan=lifespan,
        description="agent.md 平台：LangGraph 主图 + 前端兼容 REST/SSE",
    )

    from config import settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Session-Id"],
    )
    # 中间件装配顺序对齐 backend/main.py（Starlette 后加的在外层）：
    #   ChainLog(最外) → Auth → RequestLog → CORS(最内)
    if enable_middleware:
        app.add_middleware(RequestLogMiddleware)  # type: ignore[arg-type]
        app.add_middleware(AuthMiddleware)  # type: ignore[arg-type]
        app.add_middleware(ChainLogMiddleware)  # type: ignore[arg-type]

    if include_app:
        _include_app_routers(app)

    # ── 平台路由：chat/sessions/admin 用平台图实现（覆盖 app 的同名端点）──
    app.include_router(chat.router, prefix="/api")
    app.include_router(compat.router, prefix="/api")
    app.include_router(sessions.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")
    # agent.md §9 约定裸 `/chat` + `/chat/stream`（非 /api 前缀）；与 /api/chat* 同一实现。
    app.include_router(chat.router)

    # ── 统一异常 → {code,message,data,detail} ──
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return error_response(exc.code, exc.message, exc.status, exc.data)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        return error_response(exc.status_code, str(exc.detail), exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return error_response(422, "请求参数校验失败", 422)

    @app.get("/", tags=["health"])
    async def root() -> dict:
        return {"service": "Agent Platform", "version": "1.0.0", "docs": "/docs"}

    @app.get("/health", tags=["health"])
    async def health() -> dict:
        vs = getattr(app.state, "vector_store", None)
        return {
            "status": "ok",
            "app": "agent-platform",
            "vector_store_size": getattr(vs, "count", 0) if vs is not None else 0,
        }

    @app.get("/api/monitor/stats", tags=["Monitor"])
    async def monitor_stats(window_hours: float = 24.0) -> dict:
        stats = get_stats()
        stats["recent"] = get_recent_stats(window_hours)
        return stats

    @app.get("/api/monitor/usage", tags=["Monitor"])
    async def monitor_usage(request: Request) -> dict:
        uid = request.headers.get("x-user-id", "anonymous")
        store = app.state.platform.sessions
        sessions = [s for s in store.list(user_id=uid) if s.kind == "multi-agent"]
        return {
            "user_id": uid,
            "sessions": len(sessions),
            "requests": sum(len(s.prompts) for s in sessions),
            "tokens_input": sum(int((s.tokens or {}).get("input", 0)) for s in sessions),
            "tokens_output": sum(int((s.tokens or {}).get("output", 0)) for s in sessions),
        }

    return app


app = create_app(include_app=True)
