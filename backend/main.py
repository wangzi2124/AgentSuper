import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import documents, chat, skills, plugins, vectors, generated, permission as perm_api, config, weather
from app.api.chat import MAX_CONCURRENT_AGENTS
from app.monitor import RequestLogMiddleware, get_stats
from app.runtime import ensure_runtime_state
from app.session import SessionService, init_db
from app.session import task_bridge
from app.session.agent_executor import build_executor
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
    # Session 管理（session.db）：建表 + 注入执行体（agent 惰性读取）
    init_db()
    app.state.session_service = SessionService(executor=build_executor(app), global_limit=MAX_CONCURRENT_AGENTS)
    # 启动 Agent Bus 事件循环（需要在主事件循环中调用 asyncio.create_task）
    agent_bus = getattr(app.state, "agent_bus", None)
    if agent_bus:
        task_bridge.bind_bus(agent_bus)
        agent_bus.start_all()
    weather.load_weather_on_startup()
    try:
        yield
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Knowledge Base System",
    description="RAG-powered knowledge base with AI agent (LangChain + LangGraph + ChromaDB)",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestLogMiddleware)  # type: ignore

app.include_router(documents.router, prefix="/api/documents", tags=["Documents"])
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])
app.include_router(sessions_router, prefix="/api/sessions", tags=["Sessions"])
app.include_router(skills.router, prefix="/api/skills", tags=["Skills"])
app.include_router(plugins.router, prefix="/api/plugins", tags=["Plugins"])
app.include_router(vectors.router, prefix="/api/vectors", tags=["Vectors"])
app.include_router(generated.router, prefix="/api/generated", tags=["Generated"])
app.include_router(perm_api.router, prefix="/api", tags=["Permission"])
app.include_router(config.router, prefix="/api/config", tags=["Config"])
app.include_router(weather.router, prefix="/api", tags=["Weather"])


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
